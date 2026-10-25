package com.eternaltimer.engine

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

interface KeyValue {
    fun get(key: String): String?
    fun put(key: String, value: String?)
}

const val DEFAULT_SERVER = "https://ntfy.sh"

class SyncConfig(val secret: String, val server: String) {
    val topic: String
    val key: ByteArray

    init {
        val d = Crypto.derive(Crypto.parseSecret(secret))
        topic = d.first
        key = d.second
    }
}

/** Minimal ntfy client (plain HTTP, no SDK): publish, poll cached messages, live SSE. */
class NtfyClient(server: String, private val topic: String) {
    private val base = server.trimEnd('/')
    @Volatile private var current: HttpURLConnection? = null

    private fun open(url: String, readTimeoutMs: Int): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 15000
        c.readTimeout = readTimeoutMs
        c.setRequestProperty("User-Agent", "eternal-timer")
        return c
    }

    private fun q(s: String) = URLEncoder.encode(s, "UTF-8")

    fun publish(text: String) {
        val c = open("$base/$topic", 25000)
        try {
            c.requestMethod = "POST"
            c.doOutput = true
            c.setRequestProperty("Content-Type", "text/plain")
            c.setRequestProperty("Cache", "yes")
            c.outputStream.use { it.write(text.toByteArray()) }
            if (c.responseCode !in 200..299) throw java.io.IOException("relay answered ${c.responseCode}")
            c.inputStream.use { it.readBytes() }
        } finally {
            c.disconnect()
        }
    }

    fun poll(since: String): List<JObj> {
        val c = open("$base/$topic/json?poll=1&since=${q(since)}", 25000)
        try {
            if (c.responseCode !in 200..299) throw java.io.IOException("relay answered ${c.responseCode}")
            val out = ArrayList<JObj>()
            BufferedReader(InputStreamReader(c.inputStream)).useLines { lines ->
                for (line in lines) {
                    val ev = Json.parseObject(line.trim()) ?: continue
                    if (ev.str("event") == "message") out.add(ev)
                }
            }
            return out
        } finally {
            c.disconnect()
        }
    }

    /** Blocks, feeding messages to onMessage, until the stream ends, errors, or abort() is called. */
    fun stream(since: String, onOpen: () -> Unit, onMessage: (JObj) -> Unit) {
        val c = open("$base/$topic/sse?since=${q(since)}", 90000)
        current = c
        try {
            c.setRequestProperty("Accept", "text/event-stream")
            if (c.responseCode !in 200..299) throw java.io.IOException("relay answered ${c.responseCode}")
            onOpen()
            BufferedReader(InputStreamReader(c.inputStream)).use { r ->
                while (true) {
                    val line = r.readLine() ?: return
                    if (!line.startsWith("data:")) continue
                    val ev = Json.parseObject(line.substring(5).trim()) ?: continue
                    if (ev.str("event") == "message") onMessage(ev)
                }
            }
        } finally {
            current = null
            c.disconnect()
        }
    }

    fun abort() {
        try { current?.disconnect() } catch (_: Exception) {}
    }
}

/** Keeps the SSE link open with exponential back-off. Callbacks run on this thread. */
class Subscriber(
    private val cfg: SyncConfig,
    private val kv: KeyValue,
    private val onPayload: (JObj) -> Unit,
    private val onStatus: (String) -> Unit,
) : Thread("sync-subscriber") {
    private val stopFlag = AtomicBoolean(false)
    private val client = NtfyClient(cfg.server, cfg.topic)
    private val publisher = NtfyClient(cfg.server, cfg.topic)

    init { isDaemon = true }

    fun shutdown() {
        stopFlag.set(true)
        client.abort()
        interrupt()
    }

    fun publish(payload: JObj) = publisher.publish(SyncLogic.pack(payload, cfg.key))

    override fun run() {
        var backoff = 2L
        while (!stopFlag.get()) {
            try {
                onStatus("connecting")
                val since = kv.get("sync_last_id") ?: "all"
                client.stream(since, onOpen = { onStatus("online") }) { ev ->
                    backoff = 2L
                    SyncLogic.unpack(ev.str("message") ?: "", cfg.key)?.let(onPayload)
                    ev.str("id")?.let { kv.put("sync_last_id", it) }
                }
            } catch (_: InterruptedException) {
                break
            } catch (_: Exception) {
            }
            if (stopFlag.get()) break
            onStatus("offline")
            try { sleep(backoff * 1000) } catch (_: InterruptedException) { break }
            backoff = minOf(60L, backoff * 2)
        }
    }
}

/**
 * Ties the Store to the relay: merges incoming changes, pushes local ones (debounced),
 * answers "hello" with a full snapshot. Pure JVM so it can be tested off-device.
 */
class SyncController(
    private val store: Store,
    private val kv: KeyValue,
    private val onStatus: (String) -> Unit = {},
) {
    @Volatile var status = "off"
        private set
    private var cfg: SyncConfig? = null
    private var sub: Subscriber? = null
    @Volatile private var pushAll = false
    @Volatile private var lastSnapshotMs = 0L
    @Volatile private var retryDelayMs = 0L
    @Volatile private var notBeforeMs = 0L
    @Volatile var retryBaseMs = 10_000L
    private val exec = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "sync-push").also { it.isDaemon = true } }
    private var pending: ScheduledFuture<*>? = null
    private val listener: () -> Unit = { pushSoon() }

    fun config(): SyncConfig? {
        val secret = kv.get("sync_secret") ?: return null
        return try { SyncConfig(secret, kv.get("sync_server") ?: DEFAULT_SERVER) } catch (e: Exception) { null }
    }

    fun configure(secret: String, server: String) {
        Crypto.parseSecret(secret)                          // validate
        kv.put("sync_secret", secret.uppercase().trim())
        kv.put("sync_server", server.trim().ifBlank { DEFAULT_SERVER })
        kv.put("sync_last_id", null)
    }

    fun turnOff() {
        stop()
        kv.put("sync_secret", null)
        kv.put("sync_last_id", null)
        setStatus("off")
    }

    /** Start the live connection unless it is already running. */
    @Synchronized
    fun ensureStarted() {
        if (sub == null) start()
    }

    @Synchronized
    fun start() {
        stop()
        cfg = config()
        val c = cfg ?: run { setStatus("off"); return }
        store.addListener(listener)
        pushAll = true
        sub = Subscriber(c, kv, ::onPayload, ::onSubStatus).also { it.start() }
    }

    @Synchronized
    fun stop() {
        store.removeListener(listener)
        sub?.shutdown()
        sub = null
        if (status != "off") setStatus("off")
    }

    private fun setStatus(s: String) {
        status = s
        onStatus(s)
    }

    private fun onSubStatus(s: String) {
        setStatus(s)
        if (s == "online") {
            exec.execute {
                try { sub?.publish(linkedMapOf("v" to 1L, "t" to "hello", "dev" to store.deviceId)) } catch (_: Exception) {}
            }
            pushAll = true
            pushSoon()
        }
    }

    private fun onPayload(p: JObj) {
        var snap = false
        store.mutateIf { st ->
            val (changed, s) = SyncLogic.handlePayload(st, p, store.deviceId, store.now())
            snap = s
            changed
        }
        if (snap && System.currentTimeMillis() - lastSnapshotMs > 30_000) {
            // a peer said hello: resend everything, at most once per 30 s (avoids message storms)
            pushAll = true
            lastSnapshotMs = System.currentTimeMillis()
        }
        pushSoon()
    }

    @Synchronized
    fun pushSoon() {
        if (cfg == null) return
        pending?.cancel(false)
        val wait = maxOf(300L, notBeforeMs - System.currentTimeMillis())      // honour backoff after a failure
        pending = exec.schedule({ pushNow() }, wait, TimeUnit.MILLISECONDS)
    }

    private fun pushNow() {
        val s = sub ?: return
        if (status != "online") return
        var out: List<SyncLogic.Outgoing> = emptyList()
        val force = pushAll
        store.mutateIf { st ->
            val stamped = SyncLogic.ensureStamped(st, store.deviceId, store.now())
            out = SyncLogic.collectOutgoing(st, store.deviceId, force)
            stamped
        }
        pushAll = false
        for (o in out) {
            try {
                s.publish(o.payload)
            } catch (e: Exception) {
                // e.g. rate-limited: keep the live connection, retry later with growing delay
                pushAll = pushAll || force
                retryDelayMs = if (retryDelayMs == 0L) retryBaseMs else minOf(120_000L, retryDelayMs * 2)
                notBeforeMs = System.currentTimeMillis() + retryDelayMs
                pushSoon()
                return
            }
            retryDelayMs = 0L
            notBeforeMs = 0L
            store.mutateIf { st -> SyncLogic.markPushed(st, o.kind, o.id, o.rev) }
        }
    }

    /** One blocking pull+push round (used for a quick catch-up without the live connection). */
    fun syncOnce(sayHello: Boolean = false): Triple<Int, Int, Int> {
        val c = config() ?: throw IllegalStateException("Sync is not set up")
        val client = NtfyClient(c.server, c.topic)
        var received = 0; var applied = 0; var sent = 0
        var want = sayHello
        var last = kv.get("sync_last_id")
        for (ev in client.poll(last ?: "all")) {
            last = ev.str("id") ?: last
            val p = SyncLogic.unpack(ev.str("message") ?: "", c.key) ?: continue
            received++
            store.mutateIf { st ->
                val (ch, s) = SyncLogic.handlePayload(st, p, store.deviceId, store.now())
                if (ch) applied++
                want = want || s
                ch
            }
        }
        if (sayHello) client.publish(SyncLogic.pack(linkedMapOf("v" to 1L, "t" to "hello", "dev" to store.deviceId), c.key))
        var out: List<SyncLogic.Outgoing> = emptyList()
        store.mutateIf { st ->
            val stamped = SyncLogic.ensureStamped(st, store.deviceId, store.now())
            out = SyncLogic.collectOutgoing(st, store.deviceId, want && !sayHello)
            stamped
        }
        for (o in out) {
            client.publish(SyncLogic.pack(o.payload, c.key))
            store.mutateIf { st -> SyncLogic.markPushed(st, o.kind, o.id, o.rev) }
            sent++
        }
        if (last != null) kv.put("sync_last_id", last)
        return Triple(received, applied, sent)
    }
}
