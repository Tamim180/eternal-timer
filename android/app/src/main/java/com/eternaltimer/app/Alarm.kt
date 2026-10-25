package com.eternaltimer.app

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.Icon
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.net.Uri
import android.os.SystemClock
import android.util.Log
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.Gravity
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView

object Notifications {
    const val ALARM = "alarm"
    const val SYNC = "sync"
    const val TIMERS = "timers"

    fun createChannels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(ALARM, "Timer alarms", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Rings when a timer finishes"
            setSound(null, null)               // the alarm service plays the sound itself
            enableVibration(false)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        })
        nm.createNotificationChannel(NotificationChannel(TIMERS, "Running timers", NotificationManager.IMPORTANCE_LOW).apply {
            description = "A live countdown for each timer, with a Pause button"
            setShowBadge(false)
        })
        nm.createNotificationChannel(NotificationChannel(SYNC, "Sync & next timer", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Keeps your timers in sync and shows the next one"
            setShowBadge(false)
        })
    }
}

/**
 * Exact alarms for every running timer. Because the timers are stored locally
 * (and synced ahead of time), these fire even if the other device is dead.
 */
object AlarmScheduler {
    const val ACTION = "com.eternaltimer.ALARM"

    @Synchronized
    fun reschedule(ctx: Context) {
        val app = App.instance
        val am = ctx.getSystemService(AlarmManager::class.java)
        val pending = app.store.pendingFinishTimes()
        val prev = app.prefs.getStringSet("scheduled", emptySet()) ?: emptySet()
        for (id in prev) if (id !in pending) am.cancel(pi(ctx, id))
        for ((id, t) in pending) set(ctx, am, id, t)
        app.prefs.edit().putStringSet("scheduled", HashSet(pending.keys)).apply()
    }

    private fun pi(ctx: Context, id: String): PendingIntent =
        PendingIntent.getBroadcast(ctx, id.hashCode(),
            Intent(ctx, AlarmReceiver::class.java).setAction(ACTION).putExtra("id", id),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    private fun set(ctx: Context, am: AlarmManager, id: String, epochSeconds: Double) {
        val at = (epochSeconds * 1000).toLong()
        try {
            val show = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE)
            am.setAlarmClock(AlarmManager.AlarmClockInfo(at, show), pi(ctx, id))     // exact, survives Doze
        } catch (e: SecurityException) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi(ctx, id))        // no exact-alarm permission
        }
    }
}

class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val app = App.instance
        val labels = app.store.finishDue()
        if (labels.isNotEmpty()) AlarmService.start(ctx, labels)
        else AlarmScheduler.reschedule(ctx)       // fired a hair early: re-arm
    }
}

/** After reboot / app update / clock change: re-arm alarms, catch up, restart live sync. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val app = App.instance
        val labels = app.store.finishDue()          // timers that ended while the phone was off
        if (labels.isNotEmpty()) AlarmService.start(ctx, labels)
        AlarmScheduler.reschedule(ctx)
        TimerNotifications.update(ctx)
        if (app.liveSync && app.sync.config() != null) {
            try { SyncService.start(ctx) } catch (_: Exception) {}
        }
    }
}

/**
 * Rings until the user stops it, with several independent ways to end:
 *  - Dismiss button, tapping the notification (opens the full-screen screen), or SWIPING the notification away
 *  - the in-app "Alarm ringing" banner
 *  - a time limit (Settings -> Ring for), enforced two ways: a timer AND by counting elapsed time between plays
 * Every path funnels into stopAlarm(), which is idempotent.
 */
class AlarmService : Service() {
    private var player: MediaPlayer? = null
    private var wake: PowerManager.WakeLock? = null
    private val handler = Handler(Looper.getMainLooper())
    private var startedAt = 0L
    private var maxMs = 90_000L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            Log.i(TAG, "stop requested (dismiss / swipe / banner)")
            stopAlarm()
            return START_NOT_STICKY
        }
        val prefs = App.instance.prefs
        maxMs = prefs.getInt("alarm_secs", 90).coerceIn(10, 600) * 1000L
        val labels = intent?.getStringArrayListExtra(EXTRA_LABELS) ?: arrayListOf("Timer")
        val text = labels.joinToString(", ") + " finished"
        val full = PendingIntent.getActivity(this, 1,
            Intent(this, AlarmActivity::class.java).putStringArrayListExtra(EXTRA_LABELS, ArrayList(labels))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 2, Intent(this, AlarmService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, Notifications.ALARM)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle("Eternal Timer")
            .setContentText(text)
            .setCategory(Notification.CATEGORY_ALARM)
            .setOngoing(true)
            .setContentIntent(full)
            .setFullScreenIntent(full, true)
            .setDeleteIntent(stop)          // swiping the notification away also stops the alarm
            .addAction(Notification.Action.Builder(
                Icon.createWithResource(this, android.R.drawable.ic_menu_close_clear_cancel), "Dismiss", stop).build())
            .build()
        if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
        else startForeground(NOTIF_ID, n)
        startedAt = SystemClock.elapsedRealtime()
        startPlayback(prefs)
        ringing = true
        handler.removeCallbacksAndMessages(null)
        handler.postDelayed({
            Log.i(TAG, "time limit reached (${maxMs / 1000}s): stopping")
            stopAlarm()
        }, maxMs)
        Log.i(TAG, "alarm started for $labels, limit ${maxMs / 1000}s")
        return START_NOT_STICKY
    }

    /** Custom sound (if chosen) first, then the phone's default alarm, then its notification sound. */
    private fun candidateSounds(prefs: android.content.SharedPreferences): List<Uri> {
        val list = ArrayList<Uri>()
        prefs.getString("alarm_uri", null)?.let { list.add(Uri.parse(it)) }
        RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_ALARM)?.let { list.add(it) }
        RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)?.let { list.add(it) }
        RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)?.let { list.add(it) }
        return list
    }

    private fun startPlayback(prefs: android.content.SharedPreferences) {
        try {
            val pm = getSystemService(PowerManager::class.java)
            if (wake?.isHeld == true) wake?.release()
            wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "eternaltimer:alarm").also { it.acquire(maxMs + 5_000) }
        } catch (e: Exception) {
            Log.w(TAG, "wake lock failed", e)
        }
        try { player?.release() } catch (_: Exception) {}
        player = null
        for (uri in candidateSounds(prefs)) {
            try {
                val mp = MediaPlayer()
                mp.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                mp.setDataSource(this, uri)
                mp.isLooping = false                       // we loop by hand so we can also check elapsed time
                mp.setOnCompletionListener { m -> onTrackDone(m) }
                mp.setOnErrorListener { _, what, extra ->
                    Log.w(TAG, "player error $what/$extra: stopping")
                    stopAlarm(); true
                }
                mp.prepare()
                mp.start()
                player = mp
                Log.i(TAG, "playing $uri")
                break
            } catch (e: Exception) {
                Log.w(TAG, "could not play $uri", e)
            }
        }
        if (prefs.getBoolean("alarm_vibrate", true)) {
            try {
                getSystemService(Vibrator::class.java)
                    .vibrate(VibrationEffect.createWaveform(longArrayOf(0, 700, 500), 0))
            } catch (_: Exception) {
            }
        }
    }

    private fun onTrackDone(m: MediaPlayer) {
        if (SystemClock.elapsedRealtime() - startedAt >= maxMs) {
            Log.i(TAG, "time limit reached between plays: stopping")
            stopAlarm()
            return
        }
        try { m.seekTo(0); m.start() } catch (e: Exception) { stopAlarm() }
    }

    private fun stopAlarm() {
        ringing = false
        handler.removeCallbacksAndMessages(null)
        try { player?.stop() } catch (_: Exception) {}
        try { player?.release() } catch (_: Exception) {}
        player = null
        try { getSystemService(Vibrator::class.java).cancel() } catch (_: Exception) {}
        try { if (wake?.isHeld == true) wake?.release() } catch (_: Exception) {}
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        stopAlarm()
        super.onDestroy()
    }

    companion object {
        const val TAG = "EternalTimer"
        const val ACTION_STOP = "com.eternaltimer.STOP_ALARM"
        const val EXTRA_LABELS = "labels"
        const val NOTIF_ID = 1001

        /** True while an alarm is sounding (the main screen shows a Stop banner). */
        @Volatile var ringing = false

        fun start(ctx: Context, labels: List<String>) {
            val i = Intent(ctx, AlarmService::class.java).putStringArrayListExtra(EXTRA_LABELS, ArrayList(labels))
            ctx.startForegroundService(i)
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, AlarmService::class.java).setAction(ACTION_STOP))
        }
    }
}

/** Full-screen "timer finished" screen shown over the lock screen. */
class AlarmActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val labels = intent.getStringArrayListExtra(AlarmService.EXTRA_LABELS) ?: arrayListOf("Timer")
        val d = resources.displayMetrics.density
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            background = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(Color.parseColor("#2a2447"), Color.parseColor("#0c0d15")))
            setPadding((32 * d).toInt(), (32 * d).toInt(), (32 * d).toInt(), (32 * d).toInt())
        }
        root.addView(TextView(this).apply {
            text = "\u23F0"; textSize = 64f; gravity = Gravity.CENTER
        })
        root.addView(TextView(this).apply {
            text = labels.joinToString("\n"); textSize = 30f; setTextColor(Color.WHITE)
            gravity = Gravity.CENTER; setPadding(0, (16 * d).toInt(), 0, 0)
        })
        root.addView(TextView(this).apply {
            text = "finished"; textSize = 18f; setTextColor(Color.parseColor("#9d9ab8")); gravity = Gravity.CENTER
        })
        root.addView(TextView(this).apply {
            text = "Dismiss"; textSize = 20f; setTextColor(Color.WHITE); gravity = Gravity.CENTER
            setPadding((48 * d).toInt(), (18 * d).toInt(), (48 * d).toInt(), (18 * d).toInt())
            background = GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,
                intArrayOf(Color.parseColor("#7c6cf0"), Color.parseColor("#ec6eaa"))).apply { cornerRadius = 40 * d }
            setOnClickListener { AlarmService.stop(this@AlarmActivity); finish() }
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = (48 * d).toInt()
        })
        setContentView(root)
    }
}
