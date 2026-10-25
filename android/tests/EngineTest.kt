import com.eternaltimer.engine.*
import java.io.File

class FileIO(private val f: File) : StateIO {
    override fun read(): String? = if (f.exists()) f.readText() else null
    override fun write(text: String) { f.writeText(text) }
}

class FileKV(private val f: File) : KeyValue {
    private val m: MutableMap<String, Any?> = (if (f.exists()) Json.parseObject(f.readText()) else null) ?: LinkedHashMap()
    @Synchronized override fun get(key: String): String? = m[key] as? String
    @Synchronized override fun put(key: String, value: String?) { if (value == null) m.remove(key) else m[key] = value; f.writeText(Json.stringify(m)) }
}

fun fail(msg: String): Nothing { println("FAIL: $msg"); System.exit(1); throw IllegalStateException() }
fun check(cond: Boolean, msg: String) { if (!cond) fail(msg) else println("ok: $msg") }

fun main(args: Array<String>) {
    val cmd = args[0]
    if (cmd == "vectors") {
        val v = Json.parseObject(File(args[1]).readText())!!
        val secret = Crypto.parseSecret(v.str("code")!!)
        val (topic, key) = Crypto.derive(secret)
        check(topic == v.str("topic"), "topic matches Python")
        check(Crypto.hex(key) == v.str("key"), "key matches Python")
        val nonce = v.str("nonce")!!.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
        check(Crypto.encrypt(key, v.str("plain")!!.toByteArray(), nonce) == v.str("cipher"), "ciphertext byte-identical to Python")
        check(String(Crypto.decrypt(key, v.str("cipher")!!)) == v.str("plain"), "decrypts Python ciphertext")
        check(Crypto.formatSecret(secret) == v.str("code"), "secret formatting round-trips")
        check(runCatching { Crypto.parseSecret("ABCD-EFGH") }.isFailure, "short code rejected")
        return
    }
    val dir = File(args[1]).also { it.mkdirs() }
    val kv = FileKV(File(dir, "kv.json"))
    val dev = kv.get("dev") ?: java.util.UUID.randomUUID().toString().replace("-", "").substring(0, 8).also { kv.put("dev", it) }
    val store = Store(FileIO(File(dir, "state.json")), dev)
    val ctl = SyncController(store, kv) { }
    fun byLabel(l: String) = store.snapshot().firstOrNull { it.str("label") == l }?.str("id") ?: fail("no item labelled $l")
    when (cmd) {
        "join" -> { ctl.configure(args[2], args[3]); println("joined: " + ctl.syncOnce(sayHello = true)) }
        "sync" -> println("sync: " + ctl.syncOnce())
        "add" -> println("added " + if (args[2] == "timer") store.addTimer(args[3].toDouble(), args[4]) else store.addStopwatch(args[3]))
        "pause" -> store.pause(byLabel(args[2]))
        "resume" -> store.resume(byLabel(args[2]))
        "restart" -> store.restart(byLabel(args[2]))
        "remove" -> store.remove(byLabel(args[2]))
        "rename" -> store.rename(byLabel(args[2]), args[3])
        "finish" -> println("finished: " + store.finishDue())
        "list" -> {
            val now = store.now()
            for (i in store.snapshot()) {
                val rem = TimeMath.remaining(i, now)
                println("  %-34s %-9s %s %s".format(i.str("label"), i.str("mode"),
                    if (i.bool("running")) "running" else "paused/stopped",
                    if (rem != null) TimeMath.hmsCeil(rem) + " left" else TimeMath.hms(TimeMath.elapsed(i, now).toLong())))
            }
        }
        "history" -> {
            val now = store.now()
            val item = store.snapshot().first { it.str("label") == args[2] }
            val (rows, t) = HistoryView.rows(item, now)
            for (r in rows) println("  %-28s %s%s".format(HistoryView.whenText(r, now), r.title, r.duration?.let { "  (" + TimeMath.durationLong(it) + ")" } ?: ""))
            println("  pauses=${t.pauses} downtimes=${t.downtimes}")
        }
        "live" -> {
            ctl.start()
            Thread.sleep((args[2].toDouble() * 1000).toLong())
            println("live status: " + ctl.status)
            ctl.stop()
        }
        else -> fail("unknown command $cmd")
    }
}
