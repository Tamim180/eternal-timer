package com.eternaltimer.app

import android.app.Activity
import android.app.Dialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.media.RingtoneManager
import android.net.Uri
import android.os.Handler
import android.provider.Settings
import android.os.Looper
import android.text.InputType
import android.text.Spannable
import android.text.SpannableString
import android.text.style.ForegroundColorSpan
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.CheckBox
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.GridLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import com.eternaltimer.engine.*

private const val SHEET_BG = "#1b1c2b"

/** Base: a rounded bottom sheet in a dialog. */
open class Sheet(protected val act: Activity) {
    protected val dialog = Dialog(act, android.R.style.Theme_Material_Dialog_NoActionBar)
    protected val root = LinearLayout(act)
    protected val content = LinearLayout(act)

    init {
        root.orientation = LinearLayout.VERTICAL
        root.background = GradientDrawable().apply {
            setColor(Color.parseColor(SHEET_BG))
            val r = act.dp(28f)
            cornerRadii = floatArrayOf(r, r, r, r, 0f, 0f, 0f, 0f)
            setStroke(act.dpi(1), Color.argb(30, 255, 255, 255))
        }
        root.setPadding(act.dpi(20), act.dpi(10), act.dpi(20), act.dpi(20))
        val handle = View(act).apply { background = Th.rounded(Color.argb(60, 255, 255, 255), act.dp(3f)) }
        root.addView(handle, LinearLayout.LayoutParams(act.dpi(40), act.dpi(5)).apply {
            gravity = Gravity.CENTER_HORIZONTAL; bottomMargin = act.dpi(12) })
        content.orientation = LinearLayout.VERTICAL
        val scroll = ScrollView(act).apply { isVerticalScrollBarEnabled = false; addView(content) }
        root.addView(scroll, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        dialog.setContentView(root)
        dialog.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            setGravity(Gravity.BOTTOM)
            setLayout(MATCH_PARENT, WRAP_CONTENT)
            setWindowAnimations(android.R.style.Animation_InputMethod)
            setDimAmount(0.6f)
            addFlags(android.view.WindowManager.LayoutParams.FLAG_DIM_BEHIND)
        }
    }

    open fun show() { dialog.show() }
    fun dismiss() { dialog.dismiss() }

    protected fun title(text: String) = TextView(act).apply {
        this.text = text; setTextColor(Th.TEXT); textSize = 20f; typeface = Typeface.DEFAULT_BOLD
        setPadding(0, 0, 0, act.dpi(10))
    }.also { content.addView(it) }

    protected fun body(text: String, color: Int = Th.SUB, size: Float = 13f) = TextView(act).apply {
        this.text = text; setTextColor(color); textSize = size
        setPadding(0, act.dpi(2), 0, act.dpi(6))
    }.also { content.addView(it) }

    protected fun button(text: String, primary: Boolean = false, onClick: () -> Unit) = TextView(act).apply {
        this.text = text; textSize = 15f; typeface = Typeface.DEFAULT_BOLD; setTextColor(Color.WHITE)
        gravity = Gravity.CENTER
        background = if (primary) Th.gradient(Th.VIOLET, Th.PINK, act.dp(16f)) else Th.rounded(Color.argb(30, 255, 255, 255), act.dp(16f))
        setOnClickListener { onClick() }
    }.also {
        content.addView(it, LinearLayout.LayoutParams(MATCH_PARENT, act.dpi(50)).apply { topMargin = act.dpi(8) })
    }

    protected fun edit(hint: String, text: String = "") = EditText(act).apply {
        this.hint = hint; setText(text); setTextColor(Th.TEXT); setHintTextColor(Th.SUB); textSize = 15f
        background = Th.rounded(Color.argb(20, 255, 255, 255), act.dp(14f), act.dpi(1), Color.argb(40, 255, 255, 255))
        setPadding(act.dpi(14), act.dpi(12), act.dpi(14), act.dpi(12))
        inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
    }.also {
        content.addView(it, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = act.dpi(8) })
    }
}

// ---------------------------------------------------------------- add timer / stopwatch

class AddSheet(act: Activity, private val store: Store) : Sheet(act) {
    private var timerMode = true
    private val hint = TextView(act)
    private val timerBox = LinearLayout(act)
    private val tabTimer = TextView(act)
    private val tabWatch = TextView(act)
    private val wh = WheelView(act, 100)
    private val wm = WheelView(act, 60)
    private val ws = WheelView(act, 60)
    private val label: EditText
    private val presets = listOf("1m" to 60, "5m" to 300, "10m" to 600, "15m" to 900, "25m" to 1500, "45m" to 2700, "1h" to 3600)

    init {
        // mode switch
        val tabs = LinearLayout(act).apply {
            orientation = LinearLayout.HORIZONTAL
            background = Th.rounded(Color.argb(16, 255, 255, 255), act.dp(22f), act.dpi(1), Color.argb(26, 255, 255, 255))
            setPadding(act.dpi(4), act.dpi(4), act.dpi(4), act.dpi(4))
        }
        for ((tv, txt) in listOf(tabTimer to "Timer", tabWatch to "Stopwatch")) {
            tv.text = txt; tv.textSize = 14f; tv.typeface = Typeface.DEFAULT_BOLD; tv.gravity = Gravity.CENTER
            tabs.addView(tv, LinearLayout.LayoutParams(0, act.dpi(40), 1f))
        }
        tabTimer.setOnClickListener { setMode(true) }
        tabWatch.setOnClickListener { setMode(false) }
        content.addView(tabs, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))

        timerBox.orientation = LinearLayout.VERTICAL

        // captions over the wheels
        val caps = LinearLayout(act).apply { orientation = LinearLayout.HORIZONTAL }
        for (t in listOf("hours", "minutes", "seconds")) {
            caps.addView(TextView(act).apply { text = t; textSize = 12f; setTextColor(Th.SUB); gravity = Gravity.CENTER },
                LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        }
        timerBox.addView(caps, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = act.dpi(14) })

        // three wheels over a highlight band
        val frame = FrameLayout(act)
        frame.addView(View(act).apply { background = Th.rounded(Color.argb(30, 255, 255, 255), act.dp(16f)) },
            FrameLayout.LayoutParams(MATCH_PARENT, act.dpi(48), Gravity.CENTER_VERTICAL).apply {
                leftMargin = act.dpi(2); rightMargin = act.dpi(2) })
        val row = LinearLayout(act).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        fun colon() = TextView(act).apply { text = ":"; textSize = 30f; setTextColor(Th.SUB); gravity = Gravity.CENTER }
        row.addView(wh, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        row.addView(colon(), LinearLayout.LayoutParams(act.dpi(14), WRAP_CONTENT))
        row.addView(wm, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        row.addView(colon(), LinearLayout.LayoutParams(act.dpi(14), WRAP_CONTENT))
        row.addView(ws, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        frame.addView(row, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        timerBox.addView(frame)
        for (w in listOf(wh, wm, ws)) w.onChange = { updateHint() }

        hint.apply { textSize = 13f; setTextColor(Th.SUB); gravity = Gravity.CENTER; setPadding(0, act.dpi(6), 0, act.dpi(10)) }
        timerBox.addView(hint)

        val chipRow = LinearLayout(act).apply { orientation = LinearLayout.HORIZONTAL }
        for ((t, secs) in presets) {
            chipRow.addView(TextView(act).apply {
                text = t; textSize = 14f; setTextColor(Th.TEXT); typeface = Typeface.DEFAULT_BOLD; gravity = Gravity.CENTER
                background = Th.rounded(Color.argb(24, 255, 255, 255), act.dp(18f))
                setPadding(act.dpi(16), 0, act.dpi(16), 0)
                setOnClickListener { setSeconds(secs) }
            }, LinearLayout.LayoutParams(WRAP_CONTENT, act.dpi(36)).apply { rightMargin = act.dpi(8) })
        }
        timerBox.addView(HorizontalScrollView(act).apply { isHorizontalScrollBarEnabled = false; addView(chipRow) })
        content.addView(timerBox)

        label = edit("What's this for?  (optional)")
        button("\u25B6   Start", primary = true) { start() }

        wm.setValue(25, false)                 // a friendly default: 00:25:00
        setMode(true)
    }

    private fun value(): Int = wh.value * 3600 + wm.value * 60 + ws.value

    private fun setSeconds(total: Int) {
        wh.setValue(total / 3600, true)
        wm.setValue((total % 3600) / 60, true)
        ws.setValue(total % 60, true)
    }

    private fun updateHint() {
        val v = value()
        hint.text = if (v > 0) "${TimeMath.durationLong(v.toDouble())}  \u00b7  ends ${TimeMath.clock(store.now() + v, store.now())}"
        else "Spin the wheels to set a time"
    }

    private fun setMode(timer: Boolean) {
        timerMode = timer
        timerBox.visibility = if (timer) View.VISIBLE else View.GONE
        for ((tv, on) in listOf(tabTimer to timer, tabWatch to !timer)) {
            tv.setTextColor(if (on) Color.WHITE else Th.SUB)
            tv.background = if (on) Th.gradient(Th.VIOLET, Th.lerp(Th.VIOLET, Th.PINK, 0.65f), act.dp(18f)) else null
        }
        updateHint()
    }

    private fun shake(v: View) {
        v.animate().translationX(18f).setDuration(60).withEndAction {
            v.animate().translationX(-18f).setDuration(80).withEndAction {
                v.animate().translationX(0f).setDuration(60).start() }.start() }.start()
    }

    private fun start() {
        val name = label.text.toString().trim()
        if (timerMode) {
            val v = value()
            if (v <= 0) { shake(timerBox); return }
            store.addTimer(v.toDouble(), name)
            dismiss()
        } else {
            val go = { store.addStopwatch(name); dismiss() }
            if (App.instance.prefs.getBoolean("warn_stopwatch", true)) StopwatchWarning(act) { go() }.show() else go()
        }
    }
}

/** A deliberately dramatic heads-up before starting a stopwatch, which has no finish line. */
class StopwatchWarning(act: Activity, private val onConfirm: () -> Unit) : Sheet(act) {
    init {
        val (headline, joke) = LINES.random()
        title(headline)
        body(joke, Th.TEXT, 15f)
        body("(Technically nothing ticks while the app is closed. It just does the math when you come back, " +
            "and shows you exactly how long it has been \"running\".)", Th.SUB, 12f)
        val cb = CheckBox(act).apply {
            text = "Don't warn me again"; setTextColor(Th.SUB); textSize = 13f
            setPadding(act.dpi(4), act.dpi(8), 0, 0)
        }
        content.addView(cb)
        button("Start it anyway", primary = true) {
            if (cb.isChecked) App.instance.prefs.edit().putBoolean("warn_stopwatch", false).apply()
            dismiss()
            onConfirm()
        }
        button("Never mind, I'll behave") { dismiss() }
    }

    companion object {
        val LINES = listOf(
            "This stopwatch has no finish line" to "Once started, it will keep counting until you press stop \u2014 or until the universe " +
                "collapses in on itself, whichever comes first. Cosmologists say that's billions of years away. " +
                "Your attention span? Slightly less.",
            "Are you absolutely sure?" to "A stopwatch never gives up. Left alone, it will cheerfully count past your lunch, " +
                "your weekend, your next birthday and the heat death of the universe. Only you can stop it.",
            "Commitment issues? Not this stopwatch." to "It will run until you stop it, or until the last star burns out. " +
                "We're assuming you'll stop it before then. No promises about the stars.",
            "Time to make a bold promise" to "This stopwatch will tick on forever unless you press stop. " +
                "It has no off switch of its own and absolutely no sense of proportion.",
            "Warning: eternity ahead" to "Start this and it will count until the end of time. Your phone will " +
                "need a new battery long before that, though, so maybe don't wait.",
        )
    }
}

// ---------------------------------------------------------------- history

class HistorySheet(act: Activity, private val store: Store, private val id: String) : Sheet(act) {
    private val list = LinearLayout(act).apply { orientation = LinearLayout.VERTICAL }
    private val summary = TextView(act)
    private val handler = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() { render(); handler.postDelayed(this, 1000) }
    }
    private val titleTv: TextView

    init {
        titleTv = title("History")
        summary.apply { setTextColor(Th.SUB); textSize = 12f; setPadding(0, 0, 0, act.dpi(8)) }
        content.addView(summary)
        content.addView(list)
        dialog.setOnDismissListener { handler.removeCallbacks(tick) }
    }

    override fun show() { super.show(); handler.post(tick) }

    private fun render() {
        val item = store.snapshot().firstOrNull { it.str("id") == id }
        if (item == null) { dismiss(); return }
        val now = store.now()
        val (rows, t) = HistoryView.rows(item, now)
        titleTv.text = (item.str("label")?.ifBlank { null } ?: "History")
        summary.text = "${t.pauses} pause(s) \u00b7 ${TimeMath.durationLong(t.pausedSeconds)} paused  \u00b7  " +
            "${t.downtimes} offline gap(s) \u00b7 ${TimeMath.durationLong(t.downtimeSeconds)}"
        list.removeAllViews()
        for (r in rows.reversed()) {
            val color = when (r.kind) {
                "paused" -> Th.AMBER; "downtime" -> Th.RED; "finished", "resumed" -> Th.TEAL; else -> Th.LAV
            }
            val row = LinearLayout(act).apply { orientation = LinearLayout.HORIZONTAL; setPadding(0, act.dpi(7), 0, act.dpi(7)) }
            row.addView(View(act).apply { background = Th.rounded(color, act.dp(5f)) },
                LinearLayout.LayoutParams(act.dpi(10), act.dpi(10)).apply { topMargin = act.dpi(6); rightMargin = act.dpi(12) })
            val mid = LinearLayout(act).apply { orientation = LinearLayout.VERTICAL }
            mid.addView(TextView(act).apply { text = r.title; setTextColor(Th.TEXT); textSize = 15f; typeface = Typeface.DEFAULT_BOLD })
            mid.addView(TextView(act).apply { text = HistoryView.whenText(r, now); setTextColor(Th.SUB); textSize = 12f })
            if (r.detail.isNotEmpty()) mid.addView(TextView(act).apply { text = r.detail; setTextColor(Th.SUB); textSize = 11f })
            row.addView(mid, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            if (r.duration != null) {
                row.addView(TextView(act).apply {
                    text = TimeMath.durationLong(r.duration) + if (r.ongoing) " \u2026" else ""
                    setTextColor(color); textSize = 14f; typeface = Typeface.DEFAULT_BOLD
                })
            }
            list.addView(row)
        }
    }
}

// ---------------------------------------------------------------- sync pairing

class SyncSheet(act: Activity, private val app: App) : Sheet(act) {
    private val statusListener: (String) -> Unit = { act.runOnUiThread { rebuild() } }

    init {
        dialog.setOnDismissListener { app.statusListeners.remove(statusListener) }
        app.statusListeners.add(statusListener)
        rebuild()
    }

    private fun rebuild() {
        content.removeAllViews()
        title("Sync with your PC")
        val cfg = app.sync.config()
        if (cfg == null) {
            body("Timers are copied to this phone, so they keep counting and ring here even if the PC loses " +
                "power. Everything is end-to-end encrypted; the relay only sees scrambled text.")
            body("Join with the code shown on your PC", Th.TEXT, 14f)
            val code = edit("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX")
            code.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            val server = edit("Relay server", DEFAULT_SERVER)
            button("Paste from clipboard") {
                val cm = act.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                code.setText(cm.primaryClip?.getItemAt(0)?.text ?: "")
            }
            button("Join", primary = true) { join(code.text.toString(), server.text.toString()) }
            body("No PC code yet?", Th.TEXT, 14f)
            button("Create a pairing code on this phone") { join(Crypto.newSecret(), server.text.toString()) }
        } else {
            val st = app.sync.status
            val names = mapOf("online" to "Connected", "connecting" to "Connecting\u2026", "offline" to "Offline \u2014 retrying", "off" to "Off")
            body("Status:  ${names[st] ?: st}", Th.TEXT, 15f)
            body("Enter this code on your PC (cloud button \u2192 Join). Keep it private \u2014 anyone with it can read and change your timers.")
            content.addView(TextView(act).apply {
                text = cfg.secret; setTextColor(Th.LAV); textSize = 17f; typeface = Typeface.MONOSPACE
                gravity = Gravity.CENTER; setTextIsSelectable(true)
                background = Th.rounded(Color.argb(18, 255, 255, 255), act.dp(14f), act.dpi(1), Color.argb(40, 255, 255, 255))
                setPadding(act.dpi(14), act.dpi(16), act.dpi(14), act.dpi(16))
            })
            button("Copy code", primary = true) {
                (act.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                    .setPrimaryClip(ClipData.newPlainText("pairing code", cfg.secret))
                Toast.makeText(act, "Copied", Toast.LENGTH_SHORT).show()
            }
            button("Share code") {
                act.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain")
                    .putExtra(Intent.EXTRA_TEXT, cfg.secret), "Share pairing code"))
            }
            body("Relay: ${cfg.server}   \u00b7   this device: ${app.store.deviceId}", Th.SUB, 11f)
            content.addView(Switch(act).apply {
                text = "Keep sync running in the background"; setTextColor(Th.TEXT); textSize = 14f
                isChecked = app.liveSync
                setPadding(0, act.dpi(10), 0, act.dpi(2))
                setOnCheckedChangeListener { _, on ->
                    app.prefs.edit().putBoolean("live_sync", on).apply()
                    if (on) try { SyncService.start(act) } catch (_: Exception) {} else SyncService.stop(act)
                }
            })
            body("On: a timer started on the PC reaches the phone within seconds (shows a small notification). " +
                "Off: it syncs when you open the app. Alarms always ring either way.", Th.SUB, 11f)
            button("Turn sync off") {
                SyncService.stop(act)
                app.sync.turnOff()
                rebuild()
            }
        }
        body("Alarm", Th.TEXT, 14f)
        button("Test alarm sound") { AlarmService.start(act, listOf("Test alarm")) }
    }

    private fun join(code: String, server: String) {
        try {
            app.sync.configure(code, server)
        } catch (e: IllegalArgumentException) {
            Toast.makeText(act, e.message ?: "Invalid code", Toast.LENGTH_LONG).show()
            return
        }
        app.sync.start()
        if (app.liveSync) try { SyncService.start(act) } catch (_: Exception) {}
        Thread {
            try { app.sync.syncOnce(sayHello = true) } catch (_: Exception) {}
        }.start()
        rebuild()
    }
}

// ---------------------------------------------------------------- settings

class SettingsSheet(private val main: MainActivity, private val app: App) : Sheet(main) {
    init { rebuild() }

    private fun section(text: String) {
        content.addView(TextView(act).apply {
            this.text = text.uppercase(); setTextColor(Th.LAV); textSize = 11f; typeface = Typeface.DEFAULT_BOLD
            letterSpacing = 0.12f; setPadding(0, act.dpi(20), 0, act.dpi(4))
        })
    }

    private fun switchRow(label: String, sub: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
        content.addView(Switch(act).apply {
            text = label; setTextColor(Th.TEXT); textSize = 15f; isChecked = checked
            setPadding(0, act.dpi(8), 0, act.dpi(2))
            setOnCheckedChangeListener { _, on -> onChange(on) }
        })
        if (sub != null) body(sub, Th.SUB, 11f)
    }

    private fun pickRow(label: String, value: String, onClick: () -> Unit) {
        val row = LinearLayout(act).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            background = Th.rounded(Color.argb(18, 255, 255, 255), act.dp(14f))
            setPadding(act.dpi(14), act.dpi(14), act.dpi(14), act.dpi(14))
            setOnClickListener { onClick() }
        }
        row.addView(TextView(act).apply { text = label; setTextColor(Th.TEXT); textSize = 15f },
            LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        row.addView(TextView(act).apply { text = "$value  \u203A"; setTextColor(Th.SUB); textSize = 14f })
        content.addView(row, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = act.dpi(6) })
    }

    private fun chips(labels: List<String>, selected: Int, onPick: (Int) -> Unit) {
        val row = LinearLayout(act).apply { orientation = LinearLayout.HORIZONTAL }
        for ((i, t) in labels.withIndex()) {
            row.addView(TextView(act).apply {
                text = t; textSize = 14f; typeface = Typeface.DEFAULT_BOLD; gravity = Gravity.CENTER
                setTextColor(if (i == selected) Color.WHITE else Th.SUB)
                background = if (i == selected) Th.gradient(Th.VIOLET, Th.PINK, act.dp(18f)) else Th.rounded(Color.argb(24, 255, 255, 255), act.dp(18f))
                setPadding(act.dpi(16), 0, act.dpi(16), 0)
                setOnClickListener { onPick(i) }
            }, LinearLayout.LayoutParams(WRAP_CONTENT, act.dpi(38)).apply { rightMargin = act.dpi(8) })
        }
        content.addView(HorizontalScrollView(act).apply { isHorizontalScrollBarEnabled = false; addView(row) },
            LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = act.dpi(8) })
    }

    private fun soundTitle(): String {
        val s = app.prefs.getString("alarm_uri", null) ?: return "Phone default"
        return try { RingtoneManager.getRingtone(act, Uri.parse(s))?.getTitle(act) ?: "Custom" } catch (_: Exception) { "Custom" }
    }

    private fun rebuild() {
        content.removeAllViews()
        title("Settings")

        section("Sync")
        val st = mapOf("online" to "Connected", "connecting" to "Connecting\u2026", "offline" to "Offline \u2014 retrying")
        val configured = app.sync.config() != null
        body(if (configured) "Status: ${st[app.sync.status] ?: "Off"}" else "Not set up. Pair with your PC so timers are copied to this phone.",
            Th.SUB, 13f)
        button(if (configured) "Manage sync\u2026" else "Sync with PC\u2026") { SyncSheet(main, app).show() }

        section("Alarm")
        pickRow("Alarm sound", soundTitle()) {
            main.pickAlarmSound(app.prefs.getString("alarm_uri", null)) { uri ->
                app.prefs.edit().putString("alarm_uri", uri.toString()).apply()
                rebuild()
            }
        }
        body("Ring for", Th.SUB, 12f)
        val secs = listOf(30, 60, 90, 180, 300)
        chips(listOf("30 s", "1 min", "90 s", "3 min", "5 min"), secs.indexOf(app.prefs.getInt("alarm_secs", 90))) {
            app.prefs.edit().putInt("alarm_secs", secs[it]).apply(); rebuild()
        }
        switchRow("Vibrate", null, app.prefs.getBoolean("alarm_vibrate", true)) {
            app.prefs.edit().putBoolean("alarm_vibrate", it).apply()
        }
        button("Test alarm") { AlarmService.start(act, listOf("Test alarm")) }
        button("Battery settings\u2026") {
            try { act.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
            catch (_: Exception) {
                act.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + act.packageName)))
            }
        }
        body("If an alarm ever doesn't ring, exclude Eternal Timer from battery optimization.", Th.SUB, 11f)

        section("Appearance")
        chips(Th.THEME_NAMES.map { Th.themeLabel(it) }, Th.THEME_NAMES.indexOf(app.prefs.getString("theme", "aurora"))) {
            app.prefs.edit().putString("theme", Th.THEME_NAMES[it]).apply()
            Th.apply(Th.THEME_NAMES[it])
            dismiss()
            main.recreate()                       // rebuild the screen in the new colors
        }
        switchRow("Calm mode", "Less animation: flat water, still background. Easier on the battery.", Th.calm) {
            Th.calm = it
            app.prefs.edit().putBoolean("calm", it).apply()
        }

        section("Behavior")
        switchRow("Warn before starting a stopwatch", "A dramatic reminder that stopwatches run until you stop them.",
            app.prefs.getBoolean("warn_stopwatch", true)) {
            app.prefs.edit().putBoolean("warn_stopwatch", it).apply()
        }

        section("About")
        val ver = try { act.packageManager.getPackageInfo(act.packageName, 0).versionName } catch (_: Exception) { "" }
        body("Eternal Timer $ver  \u00b7  MIT license\nTimers that survive shutdowns and power cuts.", Th.SUB, 12f)
    }
}
