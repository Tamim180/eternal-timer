package com.eternaltimer.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.media.RingtoneManager
import android.net.Uri
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Choreographer
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.eternaltimer.engine.*

class MainActivity : Activity() {
    companion object { const val REQ_PICK = 42 }
    private lateinit var app: App
    private val handler = Handler(Looper.getMainLooper())
    private val cards = LinkedHashMap<String, CardView>()
    private lateinit var list: LinearLayout
    private lateinit var empty: TextView
    private lateinit var syncBtn: TextView
    private lateinit var header: LinearLayout
    private lateinit var fab: TextView
    private var visible = false
    private lateinit var banner: TextView
    private var pickCallback: ((Uri) -> Unit)? = null
    private var lastFrame = 0L
    private var lastCheck = 0L
    private val storeListener: () -> Unit = { handler.post { refreshList() } }
    private val statusListener: (String) -> Unit = { s -> handler.post { paintSync(s) } }

    private val frame = object : Choreographer.FrameCallback {
        override fun doFrame(frameTimeNanos: Long) {
            if (!visible) return
            val ms = frameTimeNanos / 1_000_000
            if (ms - lastFrame >= (if (Th.calm) 100 else 30)) {   // ~30 fps is plenty (10 in calm mode)
                lastFrame = ms
                val now = app.store.now()
                for (c in cards.values) c.tick(now)
                if (ms - lastCheck >= 1000) {
                    lastCheck = ms
                    banner.visibility = if (AlarmService.ringing) View.VISIBLE else View.GONE
                    val done = app.store.finishDue()         // timers that just hit zero while we're looking
                    if (done.isNotEmpty()) AlarmService.start(this@MainActivity, done)
                }
            }
            Choreographer.getInstance().postFrameCallback(this)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = application as App
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        if (Build.VERSION.SDK_INT >= 30) window.setDecorFitsSystemWindows(false)
        else @Suppress("DEPRECATION") window.decorView.systemUiVisibility =
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION

        val root = FrameLayout(this)
        root.addView(AuroraView(this), FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))

        val column = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            setPadding(dpi(20), dpi(12), dpi(14), dpi(8))
        }
        header.addView(TextView(this).apply {
            text = "\u221E  Eternal Timer"; setTextColor(Th.TEXT); textSize = 22f; typeface = Typeface.DEFAULT_BOLD
        }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        syncBtn = headerButton("\u2601") { SyncSheet(this, app).show() }
        header.addView(syncBtn)
        header.addView(headerButton("\u2699") { SettingsSheet(this, app).show() })
        column.addView(header, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))

        banner = TextView(this).apply {
            text = "\u23F0  Alarm ringing \u2014 tap to stop"; setTextColor(Color.WHITE); textSize = 15f
            typeface = Typeface.DEFAULT_BOLD; gravity = Gravity.CENTER; visibility = View.GONE
            background = Th.gradient(Th.RED, Th.AMBER, dp(16f))
            setPadding(dpi(16), dpi(12), dpi(16), dpi(12))
            setOnClickListener { AlarmService.stop(this@MainActivity); visibility = View.GONE }
        }
        column.addView(banner, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply {
            leftMargin = dpi(16); rightMargin = dpi(16); bottomMargin = dpi(6) })

        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dpi(16), dpi(4), dpi(16), dpi(110)) }
        empty = TextView(this).apply {
            text = "\u221E\n\nNothing running yet\nTap + to start a timer"
            setTextColor(Th.SUB); textSize = 16f; gravity = Gravity.CENTER; setPadding(0, dpi(90), 0, 0)
        }
        list.addView(empty, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        column.addView(ScrollView(this).apply { isVerticalScrollBarEnabled = false; addView(list) },
            LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        root.addView(column, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))

        fab = TextView(this).apply {
            text = "+"; textSize = 34f; setTextColor(Color.WHITE); gravity = Gravity.CENTER
            background = Th.gradient(Th.VIOLET, Th.PINK, dp(32f)).apply { shape = android.graphics.drawable.GradientDrawable.OVAL }
            elevation = dp(8f)
            setOnClickListener { AddSheet(this@MainActivity, app.store).show() }
        }
        root.addView(fab, FrameLayout.LayoutParams(dpi(64), dpi(64), Gravity.BOTTOM or Gravity.END).apply {
            rightMargin = dpi(22); bottomMargin = dpi(26)
        })
        setContentView(root)

        root.setOnApplyWindowInsetsListener { _, insets ->
            @Suppress("DEPRECATION")
            val top = insets.systemWindowInsetTop
            @Suppress("DEPRECATION")
            val bottom = insets.systemWindowInsetBottom
            header.setPadding(dpi(20), top + dpi(8), dpi(14), dpi(8))
            (fab.layoutParams as FrameLayout.LayoutParams).bottomMargin = bottom + dpi(22)
            insets
        }

        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    private fun headerButton(glyph: String, onClick: () -> Unit) = TextView(this).apply {
        text = glyph; textSize = 18f; setTextColor(Th.TEXT); gravity = Gravity.CENTER
        background = Th.rounded(Color.argb(26, 255, 255, 255), dp(20f))
        layoutParams = LinearLayout.LayoutParams(dpi(40), dpi(40)).apply { leftMargin = dpi(8) }
        setOnClickListener { onClick() }
    }

    private fun paintSync(state: String) {
        val dot = when (state) { "online" -> Th.TEAL; "connecting" -> Th.AMBER; "offline" -> Th.RED; else -> Th.SUB }
        syncBtn.background = Th.rounded(Th.alpha(dot, 60f), dp(20f), dpi(2), Th.alpha(dot, 200f))
    }

    override fun onStart() {
        super.onStart()
        app.store.addListener(storeListener)
        app.statusListeners.add(statusListener)
        paintSync(app.sync.status)
        if (app.sync.config() != null) {
            if (app.liveSync) { try { SyncService.start(this) } catch (_: Exception) { app.sync.ensureStarted() } }
            else app.sync.ensureStarted()
        }
        val done = app.store.finishDue()
        if (done.isNotEmpty()) AlarmService.start(this, done)
        refreshList()
    }

    override fun onResume() {
        super.onResume()
        visible = true
        Choreographer.getInstance().postFrameCallback(frame)
    }

    override fun onPause() {
        visible = false
        super.onPause()
    }

    override fun onStop() {
        app.store.removeListener(storeListener)
        app.statusListeners.remove(statusListener)
        if (!app.liveSync) app.sync.stop()
        super.onStop()
    }

    private fun refreshList() {
        val items = app.store.snapshot()
        val now = app.store.now()
        val seen = HashSet<String>()
        for (item in items) {
            val id = item.str("id") ?: continue
            seen.add(id)
            var card = cards[id]
            if (card == null) {
                card = CardView(this).also { c ->
                    c.toggleBtn.setOnClickListener {
                        val running = app.store.snapshot().firstOrNull { it.str("id") == id }?.bool("running") == true
                        if (running) app.store.pause(id) else app.store.resume(id)
                    }
                    c.restartBtn.setOnClickListener { app.store.restart(id) }
                    c.historyBtn.setOnClickListener { HistorySheet(this, app.store, id).show() }
                    c.removeBtn.setOnClickListener { app.store.remove(id) }
                }
                cards[id] = card
                list.addView(card, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { bottomMargin = dpi(12) })
                card.alpha = 0f
                card.translationY = dp(24f)
                card.animate().alpha(1f).translationY(0f).setDuration(380).start()
                card.ring.born = Th.mono()
                card.setOnLongClickListener {
                    rename(id, app.store.snapshot().firstOrNull { it.str("id") == id }?.str("label") ?: ""); true
                }
            }
            card.bind(item, now)
        }
        for (id in cards.keys.toList()) {
            if (id !in seen) {
                val c = cards.remove(id)!!
                c.animate().alpha(0f).translationX(dp(60f)).setDuration(240).withEndAction { list.removeView(c) }.start()
            }
        }
        empty.visibility = if (cards.isEmpty()) View.VISIBLE else View.GONE
    }

    /** Opens the system alarm-sound chooser; [cb] gets the chosen sound. */
    fun pickAlarmSound(current: String?, cb: (Uri) -> Unit) {
        pickCallback = cb
        val i = Intent(RingtoneManager.ACTION_RINGTONE_PICKER)
            .putExtra(RingtoneManager.EXTRA_RINGTONE_TYPE, RingtoneManager.TYPE_ALARM)
            .putExtra(RingtoneManager.EXTRA_RINGTONE_TITLE, "Alarm sound")
            .putExtra(RingtoneManager.EXTRA_RINGTONE_SHOW_DEFAULT, true)
            .putExtra(RingtoneManager.EXTRA_RINGTONE_SHOW_SILENT, false)
        if (current != null) i.putExtra(RingtoneManager.EXTRA_RINGTONE_EXISTING_URI, Uri.parse(current))
        startActivityForResult(i, REQ_PICK)
    }

    @Suppress("DEPRECATION")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_PICK && resultCode == RESULT_OK) {
            val uri = data?.getParcelableExtra<Uri>(RingtoneManager.EXTRA_RINGTONE_PICKED_URI)
            if (uri != null) pickCallback?.invoke(uri)
        }
        pickCallback = null
    }

    private fun rename(id: String, current: String) {
        val input = EditText(this).apply { setText(current); inputType = InputType.TYPE_CLASS_TEXT; setSelectAllOnFocus(true) }
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
            .setTitle("Rename").setView(input)
            .setPositiveButton("Save") { _, _ -> app.store.rename(id, input.text.toString().trim()) }
            .setNegativeButton("Cancel", null).show()
    }
}
