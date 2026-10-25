package com.eternaltimer.app

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Typeface
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.VelocityTracker
import android.view.View
import android.view.ViewConfiguration
import android.view.animation.DecelerateInterpolator
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * A vertical scroll wheel (like the system clock picker): drag or fling to spin, tap a faded
 * neighbour to step to it. Wraps around (59 -> 00). Ticks with a light haptic on every value.
 */
class WheelView(ctx: Context, private val count: Int) : View(ctx) {
    var onChange: ((Int) -> Unit)? = null

    private val itemH = ctx.dp(48f)
    private var pos = 0f                       // continuous position in "items"; value = round(pos) mod count
    private var lastValue = 0
    private var anim: ValueAnimator? = null
    private var tracker: VelocityTracker? = null
    private var downY = 0f
    private var lastY = 0f
    private var moved = false
    private val slop = ViewConfiguration.get(ctx).scaledTouchSlop
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        typeface = Typeface.create("sans-serif-light", Typeface.NORMAL)
    }

    val value: Int get() = mod(pos.roundToInt(), count)

    private fun mod(v: Int, n: Int) = ((v % n) + n) % n

    /** Jump (or glide, the short way round) to [v]. */
    fun setValue(v: Int, animate: Boolean) {
        val cur = pos.roundToInt()
        var d = mod(v, count) - mod(cur, count)
        if (d > count / 2) d -= count
        if (d < -count / 2) d += count
        animateTo((cur + d).toFloat(), if (animate) 320L else 0L, user = false)
    }

    private fun setPos(p: Float, user: Boolean) {
        pos = p
        val v = value
        if (v != lastValue) {
            lastValue = v
            if (user) performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
            onChange?.invoke(v)
        }
        invalidate()
    }

    private fun animateTo(target: Float, ms: Long, user: Boolean) {
        anim?.cancel()
        if (ms <= 0L || target == pos) {
            setPos(target, false)
            return
        }
        anim = ValueAnimator.ofFloat(pos, target).apply {
            duration = ms
            interpolator = DecelerateInterpolator()
            addUpdateListener { setPos(it.animatedValue as Float, user) }
            start()
        }
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(resolveSize(context.dpi(88), widthMeasureSpec), (itemH * 5).toInt())
    }

    override fun onDraw(canvas: Canvas) {
        val cy = height / 2f
        val base = pos.roundToInt()
        for (k in -3..3) {
            val idx = base + k
            val off = idx - pos
            val dist = abs(off)
            if (dist > 2.6f) continue
            paint.textSize = context.dp(40f) * (1f - 0.12f * dist)
            paint.color = if (dist < 0.5f) Th.TEXT else Th.SUB
            paint.alpha = (255 * (1f - 0.30f * dist)).coerceIn(0f, 255f).toInt()
            val y = cy + off * itemH - (paint.fontMetrics.ascent + paint.fontMetrics.descent) / 2
            canvas.drawText("%02d".format(mod(idx, count)), width / 2f, y, paint)
        }
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                parent?.requestDisallowInterceptTouchEvent(true)     // don't let the sheet scroll instead
                anim?.cancel()
                tracker?.recycle()
                tracker = VelocityTracker.obtain().also { it.addMovement(e) }
                downY = e.y; lastY = e.y; moved = false
            }
            MotionEvent.ACTION_MOVE -> {
                tracker?.addMovement(e)
                if (!moved && abs(e.y - downY) > slop) moved = true
                if (moved) {
                    setPos(pos - (e.y - lastY) / itemH, true)
                    lastY = e.y
                }
            }
            MotionEvent.ACTION_UP -> {
                tracker?.addMovement(e)
                if (!moved) {
                    performClick()
                    val off = ((e.y - height / 2f) / itemH).roundToInt()          // tapped a neighbour: step to it
                    animateTo((pos.roundToInt() + off).toFloat(), 220L, user = true)
                } else {
                    tracker?.computeCurrentVelocity(1000)
                    val vy = tracker?.yVelocity ?: 0f
                    val target = (pos - vy / itemH * 0.16f).roundToInt().toFloat()   // coast, then settle on a value
                    animateTo(target, (180 + abs(target - pos) * 45).toLong().coerceIn(180L, 900L), user = true)
                }
                tracker?.recycle(); tracker = null
            }
            MotionEvent.ACTION_CANCEL -> {
                animateTo(pos.roundToInt().toFloat(), 150L, user = true)
                tracker?.recycle(); tracker = null
            }
        }
        return true
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }
}
