package com.eternaltimer.app

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

/**
 * Keeps the live relay connection open so a timer started on the PC reaches the phone within a
 * second or two. (Each running timer has its own notification; see TimerNotifications.)
 */
class SyncService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val app = App.instance
        val n = build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(NOTIF_ID, n)
        app.sync.ensureStarted()
        return START_STICKY
    }

    private fun build(): Notification {
        val open = PendingIntent.getActivity(this, 3, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, Notifications.SYNC)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle("Eternal Timer")
            .setContentText("Keeping your timers in sync")
            .setContentIntent(open)
            .setOngoing(true)
            .setShowWhen(false)
            .build()
    }

    companion object {
        const val NOTIF_ID = 1002

        fun start(ctx: Context) {
            ctx.startForegroundService(Intent(ctx, SyncService::class.java))
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, SyncService::class.java))
        }
    }
}
