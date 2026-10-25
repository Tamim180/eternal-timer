package com.eternaltimer.app

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.net.Uri
import com.eternaltimer.engine.*

/**
 * One notification per running or paused timer / stopwatch, with a live system countdown
 * (count-up for stopwatches) and a Pause / Resume button. Kept in step with the store, so it also
 * reflects changes that arrive from the PC. A timer that reaches zero hands over to the alarm.
 */
object TimerNotifications {
    private const val BASE = 3000

    private fun nid(id: String) = BASE + (id.hashCode() and 0x0fffffff)

    @Synchronized
    fun update(ctx: Context) {
        val app = App.instance
        val nm = ctx.getSystemService(NotificationManager::class.java)
        val now = app.store.now()
        val live = HashSet<Int>()
        for (item in app.store.snapshot()) {
            val id = item.str("id") ?: continue
            if (item.str("mode") == "timer" && TimeMath.isFinished(item, now)) continue   // the alarm takes over
            val n = nid(id)
            live.add(n)
            nm.notify(n, build(ctx, item, now))
        }
        val shown = app.prefs.getStringSet("timer_notifs", emptySet()) ?: emptySet()
        for (s in shown) {
            val n = s.toIntOrNull() ?: continue
            if (n !in live) nm.cancel(n)
        }
        app.prefs.edit().putStringSet("timer_notifs", live.map { it.toString() }.toSet()).apply()
    }

    private fun build(ctx: Context, item: JObj, now: Double): Notification {
        val id = item.str("id")!!
        val timer = item.str("mode") == "timer"
        val running = item.bool("running")
        val label = item.str("label")?.ifBlank { null } ?: if (timer) "Timer" else "Stopwatch"

        val open = PendingIntent.getActivity(ctx, 4, Intent(ctx, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val toggle = PendingIntent.getBroadcast(ctx, nid(id),
            Intent(ctx, TimerActionReceiver::class.java).setAction(TimerActionReceiver.ACTION_TOGGLE)
                .setData(Uri.parse("eternaltimer://timer/$id")).putExtra("id", id),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

        val b = Notification.Builder(ctx, Notifications.TIMERS)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle(label)
            .setContentIntent(open)
            .setOnlyAlertOnce(true)
            .setOngoing(running)                     // a paused timer can be swiped away; it keeps existing
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .addAction(Notification.Action.Builder(
                Icon.createWithResource(ctx, if (running) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play),
                if (running) "Pause" else "Resume", toggle).build())

        if (timer) {
            val rem = (TimeMath.remaining(item, now) ?: 0.0).coerceAtLeast(0.0)
            val total = TimeMath.durationLong(item.dbl("target_seconds") ?: 0.0)
            if (running) {
                b.setContentText("Ends ${TimeMath.clock(now + rem, now)} \u00b7 $total")
                    .setShowWhen(true).setUsesChronometer(true).setChronometerCountDown(true)
                    .setWhen(((now + rem) * 1000).toLong())          // the system draws the countdown itself
            } else {
                b.setContentText("Paused \u00b7 ${TimeMath.hmsCeil(rem)} left \u00b7 $total").setShowWhen(false)
            }
        } else {
            val elapsed = TimeMath.elapsed(item, now)
            if (running) {
                b.setContentText("Running").setShowWhen(true).setUsesChronometer(true)
                    .setWhen(((now - elapsed) * 1000).toLong())      // counts up from the real start
            } else {
                b.setContentText("Paused \u00b7 ${TimeMath.hms(elapsed.toLong())}").setShowWhen(false)
            }
        }
        return b.build()
    }
}

/** Handles the Pause / Resume button. Works even if the app was swiped away (Android starts the process). */
class TimerActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != ACTION_TOGGLE) return
        val id = intent.getStringExtra("id") ?: return
        val store = App.instance.store
        val running = store.snapshot().firstOrNull { it.str("id") == id }?.bool("running") ?: return
        if (running) store.pause(id) else store.resume(id)      // the store listener refreshes notifications + alarms
    }

    companion object {
        const val ACTION_TOGGLE = "com.eternaltimer.TOGGLE_TIMER"
    }
}
