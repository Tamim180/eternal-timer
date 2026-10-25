package com.eternaltimer.engine

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.math.ceil
import kotlin.math.max

interface StateIO {
    fun read(): String?
    fun write(text: String)
}

object TimeMath {
    fun elapsed(item: JObj, now: Double): Double {
        val acc = item.dbl("accumulated") ?: 0.0
        val start = item.dbl("start_ts")
        return if (item.bool("running") && start != null) acc + (now - start) else acc
    }

    fun remaining(item: JObj, now: Double): Double? =
        if (item.str("mode") != "timer") null else (item.dbl("target_seconds") ?: 0.0) - elapsed(item, now)

    fun isFinished(item: JObj, now: Double): Boolean = (remaining(item, now) ?: 1.0) <= 0.0

    /** HH:MM:SS (or MM:SS under an hour) from whole seconds. */
    fun hms(seconds: Long): String {
        val s = max(0L, seconds)
        val h = s / 3600
        val m = (s % 3600) / 60
        val sec = s % 60
        return if (h > 0) "%02d:%02d:%02d".format(h, m, sec) else "%02d:%02d".format(m, sec)
    }

    fun hmsCeil(seconds: Double): String = hms(ceil(seconds - 1e-9).toLong())

    fun durationLong(seconds: Double): String {
        val s = max(0L, Math.round(seconds))
        val d = s / 86400
        val h = (s % 86400) / 3600
        val m = (s % 3600) / 60
        val sec = s % 60
        return when {
            d > 0 -> "%dd %02dh %02dm".format(d, h, m)
            h > 0 -> "%dh %02dm %02ds".format(h, m, sec)
            m > 0 -> "%dm %02ds".format(m, sec)
            else -> "%ds".format(sec)
        }
    }

    private val zone get() = ZoneId.systemDefault()

    /** '14:02:10', or 'Oct 21, 14:02:10' when not on the same day as ref. */
    fun fmtTs(ts: Double?, ref: Double): String {
        if (ts == null) return "unknown"
        val d = Instant.ofEpochMilli((ts * 1000).toLong()).atZone(zone)
        val r = Instant.ofEpochMilli((ref * 1000).toLong()).atZone(zone)
        return if (d.toLocalDate() == r.toLocalDate()) d.format(DateTimeFormatter.ofPattern("HH:mm:ss"))
        else d.format(DateTimeFormatter.ofPattern("MMM dd, HH:mm:ss"))
    }

    /** '14:32' or 'Oct 23 14:32'. */
    fun clock(ts: Double, now: Double): String {
        val d = Instant.ofEpochMilli((ts * 1000).toLong()).atZone(zone)
        val r = Instant.ofEpochMilli((now * 1000).toLong()).atZone(zone)
        return if (d.toLocalDate() == r.toLocalDate()) d.format(DateTimeFormatter.ofPattern("HH:mm"))
        else d.format(DateTimeFormatter.ofPattern("MMM dd HH:mm"))
    }
}

/** Items + tombstones, persisted as JSON. All access is synchronized; UI reads immutable snapshots. */
class Store(
    private val io: StateIO,
    val deviceId: String,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
) {
    private val lock = Any()
    private var state: JObj = readState()
    private var version = 0L
    private var cacheVersion = -1L
    private var cache: List<JObj> = emptyList()
    private val listeners = CopyOnWriteArrayList<() -> Unit>()

    fun now(): Double = clock()
    fun addListener(l: () -> Unit) { listeners.add(l) }
    fun removeListener(l: () -> Unit) { listeners.remove(l) }

    private fun readState(): JObj {
        val o = io.read()?.let { Json.parseObject(it) } ?: LinkedHashMap()
        if (o["items"] !is MutableList<*>) o["items"] = ArrayList<Any?>()
        if (o["tombstones"] !is MutableMap<*, *>) o["tombstones"] = LinkedHashMap<String, Any?>()
        return o
    }

    @Suppress("UNCHECKED_CAST")
    fun items(st: JObj = state): MutableList<JObj> = st["items"] as MutableList<JObj>

    fun find(st: JObj, id: String): JObj? = items(st).firstOrNull { it.str("id") == id }

    /** Immutable copy for the UI thread; rebuilt only when something changed. */
    fun snapshot(): List<JObj> = synchronized(lock) {
        if (cacheVersion != version) {
            cache = items().map { Json.deepCopy(it) }
            cacheVersion = version
        }
        cache
    }

    fun <T> read(block: (JObj) -> T): T = synchronized(lock) { block(state) }

    /** Run `block` under the lock; if it returns true the state is saved and listeners notified. */
    fun mutateIf(block: (JObj) -> Boolean): Boolean {
        val changed: Boolean
        synchronized(lock) {
            changed = block(state)
            if (changed) {
                version++
                io.write(Json.stringify(state))
            }
        }
        if (changed) listeners.forEach { it() }
        return changed
    }

    // ---- stamping
    private fun touch(item: JObj, at: Double = clock(), label: Boolean = false) {
        item["rev"] = item.lng("rev") + 1
        item["updated_at"] = at
        item["updated_by"] = deviceId
        item[if (label) "label_at" else "state_at"] = at
    }

    private fun log(item: JObj, type: String, ts: Double, extra: Map<String, Any?> = emptyMap()) {
        val hist = item.arr("history")
        val ev: JObj = LinkedHashMap()
        ev["type"] = type
        ev["ts"] = ts
        ev.putAll(extra)
        hist.add(ev)
        if (hist.size > MAX_EVENTS) hist.subList(1, hist.size - MAX_EVENTS + 1).clear()
    }

    private fun newItem(mode: String, label: String, target: Double?): JObj {
        val now = clock()
        val item: JObj = LinkedHashMap()
        item["id"] = UUID.randomUUID().toString().replace("-", "").substring(0, 8)
        item["label"] = label
        item["mode"] = mode
        item["start_ts"] = now
        item["accumulated"] = 0.0
        item["target_seconds"] = target
        item["running"] = true
        item["notified"] = false
        item["created_at"] = now
        item["history"] = arrayListOf<Any?>(linkedMapOf<String, Any?>("type" to "started", "ts" to now))
        item["rev"] = 0L
        touch(item, now)
        item["label_at"] = now
        return item
    }

    // ---- operations (mirror eternal_timer_core.py)
    fun addTimer(seconds: Double, label: String): String {
        var id = ""
        mutateIf { st ->
            val it = newItem("timer", label, seconds)
            id = it.str("id")!!
            items(st).add(it)
            true
        }
        return id
    }

    fun addStopwatch(label: String): String {
        var id = ""
        mutateIf { st ->
            val it = newItem("stopwatch", label, null)
            id = it.str("id")!!
            items(st).add(it)
            true
        }
        return id
    }

    fun pause(id: String) = mutateIf { st ->
        val item = find(st, id) ?: return@mutateIf false
        if (!item.bool("running")) return@mutateIf false
        val now = clock()
        item["accumulated"] = TimeMath.elapsed(item, now)
        item["running"] = false
        item["start_ts"] = null
        log(item, "paused", now)
        touch(item, now)
        true
    }

    fun resume(id: String) = mutateIf { st ->
        val item = find(st, id) ?: return@mutateIf false
        val now = clock()
        if (item.bool("running") || TimeMath.isFinished(item, now)) return@mutateIf false
        item["start_ts"] = now
        item["running"] = true
        log(item, "resumed", now)
        touch(item, now)
        true
    }

    fun restart(id: String) = mutateIf { st ->
        val item = find(st, id) ?: return@mutateIf false
        if (item.str("mode") != "timer") return@mutateIf false
        val now = clock()
        item["start_ts"] = now
        item["accumulated"] = 0.0
        item["notified"] = false
        item["running"] = true
        log(item, "restarted", now)
        touch(item, now)
        true
    }

    fun rename(id: String, label: String) = mutateIf { st ->
        val item = find(st, id) ?: return@mutateIf false
        item["label"] = label
        touch(item, label = true)
        true
    }

    fun remove(id: String) = mutateIf { st ->
        val item = find(st, id) ?: return@mutateIf false
        val now = clock()
        val tombs = st.obj("tombstones")
        tombs[id] = linkedMapOf<String, Any?>("rev" to item.lng("rev") + 1, "updated_at" to now,
            "updated_by" to deviceId, "pushed" to false)
        items(st).removeAll { it.str("id") == id }
        tombs.entries.removeAll { (it.value as? Map<*, *>)?.get("updated_at").let { t ->
            t is Number && now - t.toDouble() > 30 * 86400 } }
        true
    }

    /** Mark timers that reached zero as finished (once per device). Returns their labels. */
    fun finishDue(): List<String> {
        val out = ArrayList<String>()
        mutateIf { st ->
            val now = clock()
            var changed = false
            for (item in items(st)) {
                if (item.str("mode") != "timer" || item.bool("notified")) continue
                val rem = TimeMath.remaining(item, now) ?: continue
                if (rem > 0) continue
                val target = item.dbl("target_seconds") ?: 0.0
                val start = item.dbl("start_ts")
                val finishTs = if (item.bool("running") && start != null) start + target - (item.dbl("accumulated") ?: 0.0) else now
                item["notified"] = true
                item["accumulated"] = target
                item["running"] = false
                item["start_ts"] = null
                if (item.arr("history").none { (it as? Map<*, *>)?.get("type") == "finished" && (it as Map<*, *>)["ts"] != null }) {
                    log(item, "finished", finishTs, mapOf("noticed_ts" to now))
                    touch(item, now)
                }
                out.add(item.str("label")?.ifBlank { null } ?: "Timer")
                changed = true
            }
            changed
        }
        return out
    }

    /** Earliest finish time (epoch seconds) per running, un-notified timer. */
    fun pendingFinishTimes(): Map<String, Double> = read { st ->
        val now = clock()
        val out = LinkedHashMap<String, Double>()
        for (item in items(st)) {
            if (item.str("mode") == "timer" && item.bool("running") && !item.bool("notified")) {
                val rem = TimeMath.remaining(item, now) ?: continue
                out[item.str("id")!!] = now + rem
            }
        }
        out
    }

    companion object {
        const val MAX_EVENTS = 500
    }
}

// ---------------------------------------------------------------- history view (port of core.history_rows)

data class HistRow(val kind: String, val title: String, val start: Double?, val end: Double?,
                   val duration: Double?, val ongoing: Boolean, val detail: String)

data class HistTotals(val pauses: Int, val pausedSeconds: Double, val downtimes: Int, val downtimeSeconds: Double)

object HistoryView {
    val CAUSE = mapOf(
        "power_cut" to "Power cut / unclean shutdown", "shutdown" to "System shut down",
        "boot_app_closed" to "App not open (PC was on)", "app_closed" to "App closed",
        "app_interrupted" to "App stopped unexpectedly", "suspend" to "PC asleep",
    )

    fun rows(item: JObj, now: Double): Pair<List<HistRow>, HistTotals> {
        val events = item.arr("history").mapNotNull { @Suppress("UNCHECKED_CAST") (it as? JObj) }
            .sortedWith(compareBy<JObj>({ it.dbl("ts") != null }, { it.dbl("ts") ?: 0.0 }))
        val rows = ArrayList<HistRow>()
        val consumed = HashSet<Int>()
        var pn = 0; var ps = 0.0; var dn = 0; var ds = 0.0
        for ((i, e) in events.withIndex()) {
            if (i in consumed) continue
            val t = e.str("type")
            val ts = e.dbl("ts")
            when (t) {
                "paused" -> {
                    val j = (i + 1 until events.size).firstOrNull { events[it].str("type") in listOf("resumed", "restarted") }
                    val end: Double?; val ongoing: Boolean; val dur: Double?
                    if (j == null) { end = null; ongoing = true; dur = ts?.let { now - it } }
                    else {
                        end = events[j].dbl("ts"); ongoing = false
                        dur = if (ts != null && end != null) end - ts else null
                        if (events[j].str("type") == "resumed") consumed.add(j)
                    }
                    rows.add(HistRow("paused", "Paused", ts, end, dur, ongoing, ""))
                    pn++; ps += dur ?: 0.0
                }
                "downtime" -> {
                    val end = e.dbl("end_ts")
                    val dur = e.dbl("duration") ?: if (end != null && ts != null) end - ts else null
                    rows.add(HistRow("downtime", CAUSE[e.str("cause")] ?: "Offline", ts, end, dur, false,
                        "Kept counting while offline"))
                    dn++; ds += dur ?: 0.0
                }
                "finished" -> {
                    val noticed = e.dbl("noticed_ts")
                    val detail = if (ts != null && noticed != null && noticed - ts > 10)
                        "Noticed at ${TimeMath.fmtTs(noticed, ts)} (${TimeMath.durationLong(noticed - ts)} later)" else ""
                    rows.add(HistRow("finished", "Finished", ts, null, null, false, detail))
                }
                "started" -> rows.add(HistRow("started", "Started", ts, null, null, false, e.str("note") ?: ""))
                "resumed" -> rows.add(HistRow("resumed", "Resumed", ts, null, null, false, ""))
                "restarted" -> rows.add(HistRow("restarted", "Restarted", ts, null, null, false, "Timer reset to full duration"))
            }
        }
        return rows to HistTotals(pn, ps, dn, ds)
    }

    fun whenText(r: HistRow, now: Double): String {
        if (r.start == null && r.end == null && r.kind != "paused") return "time unknown"
        if (r.kind == "paused" || r.kind == "downtime") {
            val a = if (r.start != null) TimeMath.fmtTs(r.start, now) else "unknown"
            val b = if (r.ongoing) "now" else if (r.end != null) TimeMath.fmtTs(r.end, r.start ?: now) else "unknown"
            return "$a \u2192 $b"
        }
        return TimeMath.fmtTs(r.start, now)
    }
}
