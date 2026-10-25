package com.eternaltimer.engine

import kotlin.math.max

/** Port of the merge rules in eternal_timer_sync.py. Keep the two in lock-step. */
object SyncLogic {
    const val MAX_WIRE_CHARS = 3800
    val WIRE_FIELDS = listOf("id", "label", "mode", "start_ts", "accumulated", "target_seconds", "running",
        "created_at", "rev", "updated_at", "updated_by", "state_at", "label_at")
    val STATE_FIELDS = listOf("mode", "start_ts", "accumulated", "target_seconds", "running", "created_at", "state_at")

    class Outgoing(val kind: String, val id: String, val rev: Long, val payload: JObj)

    private fun nz(v: Double?, vararg alt: Double?): Double {
        if (v != null && v != 0.0) return v
        for (a in alt) if (a != null && a != 0.0) return a
        return 0.0
    }

    private fun stateVer(o: JObj) = nz(o.dbl("state_at"), o.dbl("updated_at")) to (o.str("updated_by") ?: "")
    private fun labelVer(o: JObj) = nz(o.dbl("label_at"), o.dbl("created_at")) to (o.str("updated_by") ?: "")
    private fun itemTime(o: JObj) = max(o.dbl("state_at") ?: 0.0, max(o.dbl("label_at") ?: 0.0, o.dbl("updated_at") ?: 0.0))

    private fun gt(a: Pair<Double, String>, b: Pair<Double, String>): Boolean =
        a.first > b.first || (a.first == b.first && a.second > b.second)

    private fun histKey(e: JObj) = Triple(e.str("type"), Math.rint((e.dbl("ts") ?: 0.0) * 10) / 10,
        Math.rint((e.dbl("end_ts") ?: 0.0) * 10) / 10)

    @Suppress("UNCHECKED_CAST")
    fun mergeHistory(first: List<Any?>, second: List<Any?>): JArr {
        val seen = HashSet<Triple<String?, Double, Double>>()
        val out = ArrayList<JObj>()
        for (x in first + second) {
            val e = x as? JObj ?: continue
            if (seen.add(histKey(e))) out.add(e)
        }
        out.sortWith(compareBy<JObj>({ it.dbl("ts") != null }, { it.dbl("ts") ?: 0.0 }))
        val capped = if (out.size > Store.MAX_EVENTS) out.take(1) + out.takeLast(Store.MAX_EVENTS - 1) else out
        return ArrayList<Any?>(capped)
    }

    fun itemToWire(item: JObj): JObj {
        val w: JObj = LinkedHashMap()
        for (k in WIRE_FIELDS) if (item.containsKey(k)) w[k] = item[k]
        w["history"] = Json.deepCopy(item.arr("history").filter { (it as? Map<*, *>)?.get("type") != "downtime" })
        return w
    }

    fun applyItem(st: JObj, w: JObj, now: Double, items: MutableList<JObj>): Boolean {
        val id = w.str("id") ?: return false
        val tombs = st.obj("tombstones")
        val t = tombs[id] as? Map<*, *>
        if (t != null && !(itemTime(w) > ((t["updated_at"] as? Number)?.toDouble() ?: 0.0))) return false
        val local = items.firstOrNull { it.str("id") == id }
        if (local == null) {
            val item: JObj = LinkedHashMap()
            for (k in WIRE_FIELDS) if (w.containsKey(k)) item[k] = w[k]
            item["history"] = Json.deepCopy(w.arr("history"))
            val rem = if (item.str("mode") == "timer") TimeMath.remaining(item, now) else null
            item["notified"] = rem != null && rem <= 0          // already over: don't ring for it
            item["pushed_rev"] = w.lng("rev")
            items.add(item)
            tombs.remove(id)
            return true
        }
        tombs.remove(id)
        val takeState = gt(stateVer(w), stateVer(local))
        val takeLabel = gt(labelVer(w), labelVer(local))
        val remoteLacks = gt(stateVer(local), stateVer(w)) || gt(labelVer(local), labelVer(w))
        var changed = false
        if (takeState) {
            val keepNotified = local.bool("notified")
            local["history"] = mergeHistory(w.arr("history"), local.arr("history"))
            for (k in STATE_FIELDS) if (w.containsKey(k)) local[k] = w[k]
            val over = local.str("mode") == "timer" && (TimeMath.remaining(local, now) ?: 1.0) <= 0
            local["notified"] = if (over) keepNotified else false
            changed = true
        }
        if (takeLabel) {
            local["label"] = w["label"] ?: ""
            local["label_at"] = w["label_at"]
            changed = true
        }
        if (changed || remoteLacks) {
            local["updated_at"] = max(local.dbl("updated_at") ?: 0.0, w.dbl("updated_at") ?: 0.0)
            if (changed && !remoteLacks) local["updated_by"] = w["updated_by"]
        }
        if (remoteLacks) {
            local["rev"] = max(local.lng("rev"), w.lng("rev")) + 1      // push the merged result back
        } else {
            local["rev"] = max(local.lng("rev"), w.lng("rev"))
            local["pushed_rev"] = local["rev"]
        }
        return changed
    }

    fun applyTomb(st: JObj, t: JObj, items: MutableList<JObj>): Boolean {
        val id = t.str("id") ?: return false
        val tombs = st.obj("tombstones")
        val upd = t.dbl("updated_at") ?: return false
        val local = items.firstOrNull { it.str("id") == id }
        if (local != null && !(upd > itemTime(local))) return false
        val cur = tombs[id] as? Map<*, *>
        if (cur != null && !(upd > ((cur["updated_at"] as? Number)?.toDouble() ?: 0.0))) return false
        items.removeAll { it.str("id") == id }
        tombs[id] = linkedMapOf<String, Any?>("rev" to t.lng("rev"), "updated_at" to upd,
            "updated_by" to (t.str("updated_by") ?: ""), "pushed" to true)
        return true
    }

    /** -> (stateChanged, peerWantsSnapshot) */
    @Suppress("UNCHECKED_CAST")
    fun handlePayload(st: JObj, payload: JObj, myDev: String, now: Double): Pair<Boolean, Boolean> {
        if (payload.str("dev") == myDev) return false to false
        val items = st["items"] as MutableList<JObj>
        return when (payload.str("t")) {
            "item" -> (payload["item"] as? JObj)?.let { applyItem(st, it, now, items) to false } ?: (false to false)
            "tomb" -> (payload["tomb"] as? JObj)?.let { applyTomb(st, it, items) to false } ?: (false to false)
            "hello" -> false to true
            else -> false to false
        }
    }

    @Suppress("UNCHECKED_CAST")
    fun ensureStamped(st: JObj, dev: String, now: Double): Boolean {
        var changed = false
        for (item in st["items"] as MutableList<JObj>) {
            if (!item.containsKey("rev")) {
                val t0 = item.dbl("created_at") ?: now
                item["rev"] = 1L; item["updated_at"] = t0; item["updated_by"] = dev
                item["state_at"] = t0; item["label_at"] = t0
                changed = true
            }
        }
        return changed
    }

    @Suppress("UNCHECKED_CAST")
    fun hasDirty(st: JObj): Boolean =
        (st["items"] as MutableList<JObj>).any { it.lng("pushed_rev") < it.lng("rev") } ||
            st.obj("tombstones").values.any { (it as? Map<*, *>)?.get("pushed") != true }

    @Suppress("UNCHECKED_CAST")
    fun collectOutgoing(st: JObj, dev: String, forceAll: Boolean): List<Outgoing> {
        val out = ArrayList<Outgoing>()
        for (item in st["items"] as MutableList<JObj>) {
            if (forceAll || item.lng("pushed_rev") < item.lng("rev")) {
                out.add(Outgoing("item", item.str("id")!!, item.lng("rev"),
                    linkedMapOf("v" to 1L, "t" to "item", "dev" to dev, "item" to itemToWire(item))))
            }
        }
        for ((tid, v) in st.obj("tombstones")) {
            val t = v as? JObj ?: continue
            if (forceAll || t["pushed"] != true) {
                out.add(Outgoing("tomb", tid, t.lng("rev"), linkedMapOf("v" to 1L, "t" to "tomb", "dev" to dev,
                    "tomb" to linkedMapOf("id" to tid, "rev" to t["rev"], "updated_at" to t["updated_at"],
                        "updated_by" to (t["updated_by"] ?: "")))))
            }
        }
        return out
    }

    @Suppress("UNCHECKED_CAST")
    fun markPushed(st: JObj, kind: String, id: String, rev: Long): Boolean {
        if (kind == "item") {
            val it = (st["items"] as MutableList<JObj>).firstOrNull { x -> x.str("id") == id } ?: return false
            if (it.lng("pushed_rev") >= rev) return false
            it["pushed_rev"] = rev
            return true
        }
        val t = st.obj("tombstones")[id] as? JObj ?: return false
        if (t.lng("rev") <= rev) { t["pushed"] = true; return true }
        return false
    }

    @Suppress("UNCHECKED_CAST")
    fun pack(payload: JObj, key: ByteArray): String {
        while (true) {
            val text = Crypto.encrypt(key, Json.stringify(payload).toByteArray())
            val hist = (payload["item"] as? JObj)?.get("history") as? MutableList<Any?>
            if (text.length <= MAX_WIRE_CHARS || hist == null || hist.size <= 2) return text
            hist.removeAt(1)
        }
    }

    fun unpack(text: String, key: ByteArray): JObj? = try {
        Json.parseObject(String(Crypto.decrypt(key, text)))
    } catch (e: Exception) { null }
}
