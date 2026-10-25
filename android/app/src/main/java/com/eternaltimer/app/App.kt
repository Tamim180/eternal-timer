package com.eternaltimer.app

import android.app.Application
import android.content.SharedPreferences
import com.eternaltimer.engine.*
import java.io.File
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList

class PrefsKV(private val p: SharedPreferences) : KeyValue {
    override fun get(key: String): String? = p.getString(key, null)
    override fun put(key: String, value: String?) {
        p.edit().apply { if (value == null) remove(key) else putString(key, value) }.apply()
    }
}

/** state.json in app-private storage; written via temp file + rename so a crash can't truncate it. */
class FileStateIO(private val dir: File) : StateIO {
    private val f = File(dir, "state.json")
    override fun read(): String? = try { if (f.exists()) f.readText() else null } catch (e: Exception) { null }
    override fun write(text: String) {
        val tmp = File(dir, "state.json.tmp")
        tmp.writeText(text)
        tmp.renameTo(f)
    }
}

class App : Application() {
    lateinit var prefs: SharedPreferences
    lateinit var store: Store
    lateinit var sync: SyncController
    val statusListeners = CopyOnWriteArrayList<(String) -> Unit>()

    val liveSync: Boolean get() = prefs.getBoolean("live_sync", true)

    override fun onCreate() {
        super.onCreate()
        instance = this
        prefs = getSharedPreferences("eternal", MODE_PRIVATE)
        val dev = prefs.getString("device_id", null)
            ?: UUID.randomUUID().toString().replace("-", "").substring(0, 8).also {
                prefs.edit().putString("device_id", it).apply()
            }
        store = Store(FileStateIO(filesDir), dev)
        sync = SyncController(store, PrefsKV(prefs)) { s -> statusListeners.forEach { it(s) } }
        Th.apply(prefs.getString("theme", "aurora") ?: "aurora")
        Th.calm = prefs.getBoolean("calm", false)
        Notifications.createChannels(this)
        // every change (local or synced) re-arms the exact alarms
        store.addListener {
            AlarmScheduler.reschedule(this)
            TimerNotifications.update(this)
        }
        AlarmScheduler.reschedule(this)
        TimerNotifications.update(this)
    }

    companion object {
        lateinit var instance: App
    }
}
