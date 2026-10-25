package com.eternaltimer.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PathMeasure
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.SweepGradient
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import com.eternaltimer.engine.*
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

object Th {
    val BG_TOP = Color.parseColor("#171826")
    val BG_BOTTOM = Color.parseColor("#0c0d15")
    var VIOLET = Color.parseColor("#7c6cf0")
    var PINK = Color.parseColor("#ec6eaa")
    var LAV = Color.parseColor("#a78bfa")
    val TEAL = Color.parseColor("#58e0b0")
    val AMBER = Color.parseColor("#f5b85a")
    val RED = Color.parseColor("#ef6a7e")
    var SKY = Color.parseColor("#4aa8e8")
    val TEXT = Color.parseColor("#f4f2ff")
    val SUB = Color.parseColor("#9d9ab8")
    val CARD_TOP = Color.parseColor("#343650")
    val CARD_BOTTOM = Color.parseColor("#24263a")

    // theme name -> violet, pink, lavender, sky   (same palettes as the desktop app)
    val THEME_NAMES = listOf("aurora", "ocean", "sunset", "mint")
    private val THEMES = mapOf(
        "aurora" to listOf("#7c6cf0", "#ec6eaa", "#a78bfa", "#4aa8e8"),
        "ocean" to listOf("#3b82f6", "#22d3ee", "#7dd3fc", "#6366f1"),
        "sunset" to listOf("#f97316", "#ec4899", "#fdba74", "#a855f7"),
        "mint" to listOf("#10b981", "#a3e635", "#6ee7b7", "#06b6d4"),
    )

    /** Reduced-motion mode: flat water, still background, slower frame rate. */
    @Volatile var calm = false

    fun themeLabel(name: String) = name.replaceFirstChar { it.uppercase() }

    fun apply(name: String) {
        val c = THEMES[name] ?: THEMES.getValue("aurora")
        VIOLET = Color.parseColor(c[0]); PINK = Color.parseColor(c[1])
        LAV = Color.parseColor(c[2]); SKY = Color.parseColor(c[3])
    }

    fun lerp(a: Int, b: Int, t: Float): Int {
        val k = t.coerceIn(0f, 1f)
        return Color.argb(
            (Color.alpha(a) + (Color.alpha(b) - Color.alpha(a)) * k).toInt(),
            (Color.red(a) + (Color.red(b) - Color.red(a)) * k).toInt(),
            (Color.green(a) + (Color.green(b) - Color.green(a)) * k).toInt(),
            (Color.blue(a) + (Color.blue(b) - Color.blue(a)) * k).toInt())
    }

    fun alpha(c: Int, a: Float): Int = Color.argb(a.coerceIn(0f, 255f).toInt(), Color.red(c), Color.green(c), Color.blue(c))

    fun easeOut(t: Double): Float { val k = t.coerceIn(0.0, 1.0); return (1 - (1 - k) * (1 - k) * (1 - k)).toFloat() }

    fun mono(): Double = System.nanoTime() / 1e9

    fun rounded(color: Int, radius: Float, stroke: Int = 0, strokeColor: Int = 0): GradientDrawable =
        GradientDrawable().apply { setColor(color); cornerRadius = radius; if (stroke > 0) setStroke(stroke, strokeColor) }

    fun gradient(c1: Int, c2: Int, radius: Float, vertical: Boolean = false): GradientDrawable =
        GradientDrawable(if (vertical) GradientDrawable.Orientation.TOP_BOTTOM else GradientDrawable.Orientation.LEFT_RIGHT,
            intArrayOf(c1, c2)).apply { cornerRadius = radius }
}

fun Context.dp(v: Float): Float = v * resources.displayMetrics.density
fun Context.dpi(v: Int): Int = (v * resources.displayMetrics.density).toInt()

/** Odometer-style text: characters slide when they change. */
class Roller(text: String) {
    private var cur = text.toCharArray()
    private var prev = CharArray(text.length)
    private var t0 = DoubleArray(text.length)
    private val dur = 0.30

    fun text() = String(cur)

    fun set(text: String) {
        if (text.length != cur.size) {
            cur = text.toCharArray(); prev = CharArray(text.length); t0 = DoubleArray(text.length)
            return
        }
        val now = Th.mono()
        for (i in text.indices) if (text[i] != cur[i]) { prev[i] = cur[i]; t0[i] = now; cur[i] = text[i] }
    }

    fun animating(): Boolean {
        val now = Th.mono()
        return cur.indices.any { prev[it] != '\u0000' && now - t0[it] < dur }
    }

    fun draw(c: Canvas, cx: Float, cy: Float, halfH: Float, p: Paint, opacity: Float) {
        val now = Th.mono()
        val dw = p.measureText("0")
        val widths = FloatArray(cur.size) { if (cur[it].isDigit()) dw else p.measureText(cur[it].toString()) }
        var x = cx - widths.sum() / 2
        val base = cy - (p.fontMetrics.ascent + p.fontMetrics.descent) / 2
        val shift = halfH * 1.1f
        c.save()
        c.clipRect(cx - widths.sum() / 2 - 4, cy - halfH, cx + widths.sum() / 2 + 4, cy + halfH)
        p.textAlign = Paint.Align.CENTER
        val baseColor = p.color
        for (i in cur.indices) {
            val mx = x + widths[i] / 2
            val t = if (prev[i] != '\u0000') (now - t0[i]) / dur else 1.0
            if (t < 1.0) {
                val e = Th.easeOut(t)
                p.color = Th.alpha(baseColor, Color.alpha(baseColor) * opacity * (1 - e))
                c.drawText(prev[i].toString(), mx, base - e * shift, p)
                p.color = Th.alpha(baseColor, Color.alpha(baseColor) * opacity * e)
                c.drawText(cur[i].toString(), mx, base + (1 - e) * shift, p)
            } else {
                prev[i] = '\u0000'
                p.color = Th.alpha(baseColor, Color.alpha(baseColor) * opacity)
                c.drawText(cur[i].toString(), mx, base, p)
            }
            x += widths[i]
        }
        p.color = baseColor
        c.restore()
    }
}

/** Liquid progress ring: timers drain, stopwatches sweep a comet once a minute. */
class RingView(ctx: Context) : View(ctx) {
    var mode = "timer"
    var elapsed = 0.0
    var secPos = 0.0
    var orbit = 0.0
    var secFrac = 0.0
    var running = true
    var urgency = 0f
    var finished = false
        set(v) { if (v && !field) finishT0 = Th.mono(); field = v }
    var born = Th.mono()
    private var finishT0 = 0.0
    val roll = Roller("00:00")

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL) }
    private val rect = RectF()
    private val path = Path()

    override fun onDraw(canvas: Canvas) {
        val S = width.toFloat()
        val k = S / 108f
        val cx = S / 2
        val cy = S / 2
        val R = S / 2 - 16 * k
        val now = Th.mono()
        val intro = Th.easeOut((now - born) / 0.9)
        rect.set(cx - R, cy - R, cx + R, cy + R)

        val c1: Int
        val c2: Int
        if (finished) { c1 = Th.TEAL; c2 = Color.parseColor("#a6f7dc") }
        else {
            var a = Th.lerp(Th.VIOLET, Th.AMBER, urgency)
            var b = Th.lerp(Th.PINK, Th.RED, urgency)
            if (!running) { a = Th.lerp(a, Th.SUB, 0.6f); b = Th.lerp(b, Th.SUB, 0.6f) }
            c1 = a; c2 = b
        }
        val dim = if (running || finished) 1f else 0.6f
        val mid = Th.lerp(c1, c2, 0.5f)

        // inner disc
        paint.reset(); paint.isAntiAlias = true
        paint.shader = RadialGradient(cx, cy, R, Color.argb(14, 255, 255, 255), Color.argb(3, 255, 255, 255), Shader.TileMode.CLAMP)
        canvas.drawCircle(cx, cy, R - 4 * k, paint)
        paint.shader = null

        // liquid
        if (mode == "timer" && !finished) {
            val lvl = ((1 - elapsed) * intro).toFloat().coerceIn(0f, 1f)
            if (lvl > 0.003f) {
                val rr = R - 6 * k
                canvas.save()
                path.reset(); path.addCircle(cx, cy, rr, Path.Direction.CW)
                canvas.clipPath(path)
                val top = cy + rr - 2 * rr * lvl
                val amp = if (Th.calm) 0f else 2.6f * k * (min(lvl, 1 - lvl) * 9).coerceIn(0f, 1f)
                val ph = now * (if (running) 1.5 else 0.45)
                for (layer in 0..1) {
                    val sc = if (layer == 0) 1f else 0.7f
                    val off = if (layer == 0) 0.0 else 1.9
                    val spd = if (layer == 0) 1.0 else -0.8
                    path.reset()
                    path.moveTo(cx - rr, cy + rr + 2 * k)
                    for (i in 0..32) {
                        val x = cx - rr + 2 * rr * i / 32
                        val u = (x - (cx - rr)) / rr
                        val y = top + amp * sc * sin(u * 3.2 + ph * spd + off).toFloat() +
                            amp * 0.4f * sin(u * 7.0 + ph * 1.3 * spd).toFloat()
                        path.lineTo(x, y)
                    }
                    path.lineTo(cx + rr, cy + rr + 2 * k)
                    path.close()
                    paint.shader = LinearGradient(0f, top - 4 * k, 0f, cy + rr,
                        Th.alpha(c2, (if (layer == 0) 86 else 54) * dim), Th.alpha(c1, (if (layer == 0) 30 else 20) * dim),
                        Shader.TileMode.CLAMP)
                    canvas.drawPath(path, paint)
                }
                paint.shader = null
                canvas.restore()
            }
        }
        // glassy highlight
        paint.style = Paint.Style.STROKE; paint.strokeWidth = 2.2f * k; paint.strokeCap = Paint.Cap.ROUND
        paint.color = Color.argb(38, 255, 255, 255)
        canvas.drawArc(RectF(cx - R + 8 * k, cy - R + 8 * k, cx + R - 8 * k, cy + R - 8 * k), -166f, 58f, false, paint)

        // dial ticks
        for (i in 0 until 60) {
            val ang = (PI / 180) * (90 - i * 6)
            val long = i % 5 == 0
            val r1 = R + 9 * k
            val r2 = R + (if (long) 15f else 12.5f) * k
            val lit: Float = when {
                finished -> (0.45 + 0.55 * (0.5 + 0.5 * sin(now * 3.2 - i * 0.22))).toFloat()
                mode == "timer" -> {
                    val lo = i / 60.0
                    val hi = (i + 1) / 60.0
                    val l = if (lo >= elapsed) 1.0 else if (hi > elapsed) ((hi - elapsed) * 60).coerceIn(0.0, 1.0) else 0.0
                    (l * intro).toFloat()
                }
                else -> if (i + 1 <= secPos.toInt()) 1f else if (i < secPos) (secPos - i).toFloat().coerceIn(0f, 1f) else 0f
            }
            val a = if (lit > 0) (36 + 219 * lit) * dim else 34f
            paint.color = Th.alpha(Th.lerp(c1, c2, i / 60f), a)
            paint.strokeWidth = (if (long) 2.2f else 1.6f) * k
            canvas.drawLine(cx + r1 * cos(ang).toFloat(), cy - r1 * sin(ang).toFloat(),
                cx + r2 * cos(ang).toFloat(), cy - r2 * sin(ang).toFloat(), paint)
        }

        // track
        paint.strokeWidth = 6f * k; paint.color = Color.argb(22, 255, 255, 255)
        canvas.drawCircle(cx, cy, R, paint)

        var head: Double? = null
        if (finished) {
            val t = now - finishT0
            paint.color = Th.alpha(Th.TEAL, 60f); paint.strokeWidth = 12f * k; canvas.drawCircle(cx, cy, R, paint)
            paint.color = Th.TEAL; paint.strokeWidth = 6f * k; canvas.drawCircle(cx, cy, R, paint)
            for (kk in 0..1) {
                if (t >= kk * 0.9) {
                    val ph = ((t - kk * 0.9) % 1.8) / 1.8
                    paint.color = Th.alpha(Th.TEAL, (130 * (1 - ph)).toFloat()); paint.strokeWidth = 2f * k
                    canvas.drawCircle(cx, cy, (R + ph * (S / 2 - R - 1)).toFloat(), paint)
                }
            }
            val prog = Th.easeOut(t / 0.5)
            val chk = Path().apply { moveTo(cx - 13 * k, cy + 1 * k); lineTo(cx - 4 * k, cy + 10 * k); lineTo(cx + 14 * k, cy - 10 * k) }
            val seg = Path()
            val pm = PathMeasure(chk, false)
            pm.getSegment(0f, pm.length * prog, seg, true)
            paint.strokeJoin = Paint.Join.ROUND
            paint.color = Th.alpha(Th.TEAL, (50 * (0.6 + 0.4 * sin(now * 3))).toFloat()); paint.strokeWidth = 11f * k
            canvas.drawPath(seg, paint)
            paint.color = Color.parseColor("#d9fff1"); paint.strokeWidth = 4.2f * k
            canvas.drawPath(seg, paint)
            return
        }

        if (mode == "timer") {
            val rf = ((1 - elapsed) * intro).coerceIn(0.0, 1.0)
            if (rf > 0.002) {
                val startAndroid = (360 * (1 - rf) - 90).toFloat()           // clockwise from 3 o'clock
                val sweep = (360 * rf).toFloat()
                paint.color = Th.alpha(mid, 46 * dim); paint.strokeWidth = 13f * k
                canvas.drawArc(rect, startAndroid, sweep, false, paint)
                val sg = SweepGradient(cx, cy, intArrayOf(mid, c2, mid, c1, mid), floatArrayOf(0f, 0.25f, 0.5f, 0.75f, 1f))
                paint.shader = sg
                paint.strokeWidth = 6f * k
                canvas.drawArc(rect, startAndroid, sweep, false, paint)
                paint.shader = null
                head = 90 - 360 * (1 - rf)
            }
        } else {
            val deg = 90 - 360 * (secPos / 60.0)
            val n = 18
            val tail = 130.0
            val step = tail / n
            for (i in 0 until n) {
                val a = Math.pow(1 - i.toDouble() / n, 1.6)
                paint.color = Th.alpha(Th.lerp(c1, c2, i / n.toFloat()), (235 * a * dim).toFloat())
                paint.strokeWidth = (6 * (1 - 0.55 * i / n)).toFloat() * k
                canvas.drawArc(rect, (-(deg + i * step)).toFloat() - (step * 1.3).toFloat(), (step * 1.3).toFloat(), false, paint)
            }
            head = deg
        }

        if (head != null) {
            val ang = head * PI / 180
            val hx = cx + R * cos(ang).toFloat()
            val hy = cy - R * sin(ang).toFloat()
            val pulse = (0.75 + 0.25 * sin(now * 4.0 + urgency * 3)).toFloat()
            paint.style = Paint.Style.FILL
            paint.shader = RadialGradient(hx, hy, 15 * k, intArrayOf(Th.alpha(Color.WHITE, 150 * pulse * dim),
                Th.alpha(mid, 95 * pulse * dim), Th.alpha(mid, 0f)), floatArrayOf(0f, 0.35f, 1f), Shader.TileMode.CLAMP)
            canvas.drawCircle(hx, hy, 15 * k, paint)
            paint.shader = null
            paint.color = Th.alpha(Color.WHITE, 255 * dim)
            canvas.drawCircle(hx, hy, 3.8f * k, paint)
        }

        // seconds orbit
        if (mode == "timer" && running) {
            val ro = R - 11 * k
            val oa = (90 - 360 * (orbit / 60.0)) * PI / 180
            paint.style = Paint.Style.FILL
            for (i in 0 until 9) {
                val a2 = oa + i * 5.5 * PI / 180
                val fade = 1 - i / 9f
                paint.color = Th.alpha(Color.WHITE, 150 * fade * fade)
                val rad = 2f * k * (0.5f + 0.5f * fade)
                canvas.drawCircle(cx + ro * cos(a2).toFloat(), cy - ro * sin(a2).toFloat(), rad, paint)
            }
        }

        // time
        val str = roll.text()
        text.textSize = (if (str.length <= 5) 23f else 14f) * k
        var scale = 1f
        if (running && urgency > 0) scale = 1f + 0.09f * ((secFrac - 0.7) / 0.3).coerceIn(0.0, 1.0).toFloat()
        val opacity = if (running) 1f else (0.55 + 0.45 * (0.5 + 0.5 * sin(now * 2.6))).toFloat()
        text.color = Th.lerp(Th.TEXT, Th.lerp(Th.AMBER, Th.RED, 0.5f), urgency)
        canvas.save()
        canvas.scale(scale, scale, cx, cy)
        roll.draw(canvas, cx, cy, 16 * k, text, opacity)
        canvas.restore()
    }
}

/** Slow-drifting aurora behind everything. */
class AuroraView(ctx: Context) : View(ctx) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var bg: LinearGradient? = null
    private var blobs: List<RadialGradient> = emptyList()
    private var t = 0.0
    private val tick = object : Runnable {
        override fun run() { if (!Th.calm) t += 0.05 * 0.7; invalidate(); postDelayed(this, if (Th.calm) 400 else 50) }
    }

    override fun onAttachedToWindow() { super.onAttachedToWindow(); post(tick) }
    override fun onDetachedFromWindow() { removeCallbacks(tick); super.onDetachedFromWindow() }

    override fun onSizeChanged(w: Int, h: Int, ow: Int, oh: Int) {
        bg = LinearGradient(0f, 0f, 0f, h.toFloat(), Th.BG_TOP, Th.BG_BOTTOM, Shader.TileMode.CLAMP)
        val r = w * 0.65f
        fun blob(c: Int, a: Int) = RadialGradient(0f, 0f, r, Th.alpha(c, a.toFloat()), Th.alpha(c, 0f), Shader.TileMode.CLAMP)
        blobs = listOf(blob(Th.VIOLET, 84), blob(Th.PINK, 58), blob(Th.SKY, 44))
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        paint.shader = bg
        c.drawRect(0f, 0f, w, h, paint)
        val pos = listOf(
            (w * 0.22 + sin(t) * w * 0.12) to (h * 0.14 + cos(t * 0.8) * h * 0.04),
            (w * 0.85 + cos(t * 0.7) * w * 0.10) to (h * 0.55 + sin(t * 0.6) * h * 0.08),
            (w * 0.30 + sin(t * 0.5 + 2) * w * 0.14) to (h * 0.98 + cos(t * 0.9) * h * 0.03))
        for ((i, b) in blobs.withIndex()) {
            c.save(); c.translate(pos[i].first.toFloat(), pos[i].second.toFloat())
            paint.shader = b
            c.drawCircle(0f, 0f, w * 0.65f, paint)
            c.restore()
        }
        paint.shader = null
    }
}

/** One timer / stopwatch card. */
class CardView(ctx: Context) : LinearLayout(ctx) {
    val ring = RingView(ctx)
    private val labelTv = TextView(ctx)
    private val pill = TextView(ctx)
    private val info = TextView(ctx)
    val toggleBtn = roundBtn("\u23F8")
    val restartBtn = roundBtn("\u21BB")
    val historyBtn = roundBtn("\u2630")
    val removeBtn = roundBtn("\u2715")
    var itemId = ""
    private var item: JObj? = null
    private var lastStatus = ""

    init {
        orientation = HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        setPadding(ctx.dpi(14), ctx.dpi(12), ctx.dpi(12), ctx.dpi(12))
        background = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(Th.CARD_TOP, Th.CARD_BOTTOM)).apply {
            cornerRadius = ctx.dp(22f); setStroke(ctx.dpi(1), Color.argb(46, 255, 255, 255))
        }
        addView(ring, LayoutParams(ctx.dpi(104), ctx.dpi(104)))
        val mid = LinearLayout(ctx).apply { orientation = VERTICAL; gravity = Gravity.CENTER_VERTICAL }
        labelTv.apply { setTextColor(Th.TEXT); textSize = 17f; typeface = Typeface.DEFAULT_BOLD; maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END }
        pill.apply { textSize = 10f; typeface = Typeface.DEFAULT_BOLD; letterSpacing = 0.1f
            setPadding(ctx.dpi(9), ctx.dpi(2), ctx.dpi(9), ctx.dpi(2)) }
        info.apply { setTextColor(Th.SUB); textSize = 12f; maxLines = 1; ellipsize = android.text.TextUtils.TruncateAt.END }
        mid.addView(labelTv)
        mid.addView(pill, LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = ctx.dpi(6); bottomMargin = ctx.dpi(6) })
        mid.addView(info)
        addView(mid, LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { leftMargin = ctx.dpi(12) })
        val grid = android.widget.GridLayout(ctx).apply { columnCount = 2; rowCount = 2 }
        for (b in listOf(toggleBtn, restartBtn, historyBtn, removeBtn)) {
            grid.addView(b, android.widget.GridLayout.LayoutParams().apply {
                width = ctx.dpi(38); height = ctx.dpi(38); setMargins(ctx.dpi(3), ctx.dpi(3), ctx.dpi(3), ctx.dpi(3)) })
        }
        addView(grid)
    }

    private fun roundBtn(glyph: String) = TextView(context).apply {
        text = glyph; textSize = 15f; setTextColor(Th.TEXT); gravity = Gravity.CENTER
        background = Th.rounded(Color.argb(24, 255, 255, 255), context.dp(19f))
    }

    private fun pillStyle(text: String, color: Int) {
        pill.text = text.uppercase()
        pill.setTextColor(color)
        pill.background = Th.rounded(Th.alpha(color, 40f), context.dp(10f))
    }

    fun bind(it: JObj, now: Double) {
        item = it
        itemId = it.str("id") ?: ""
        val timer = it.str("mode") == "timer"
        labelTv.text = it.str("label")?.ifBlank { null } ?: if (timer) "Timer" else "Stopwatch"
        val finished = timer && TimeMath.isFinished(it, now)
        val running = it.bool("running")
        val status = if (finished) "finished" else if (running) "running" else "paused"
        if (status != lastStatus) {
            lastStatus = status
            pillStyle(status, when (status) { "finished" -> Th.TEAL; "running" -> Th.LAV; else -> Th.AMBER })
        }
        info.text = if (timer) {
            val dur = TimeMath.durationLong(it.dbl("target_seconds") ?: 0.0)
            when {
                finished -> "Finished \u00b7 $dur"
                running -> "Ends ${TimeMath.clock(now + (TimeMath.remaining(it, now) ?: 0.0), now)} \u00b7 $dur"
                else -> "${TimeMath.hmsCeil(TimeMath.remaining(it, now) ?: 0.0)} left \u00b7 $dur"
            }
        } else "Started ${TimeMath.clock(it.dbl("created_at") ?: now, now)}"
        restartBtn.visibility = if (timer) VISIBLE else GONE
        toggleBtn.visibility = if (finished) INVISIBLE else VISIBLE
        toggleBtn.text = if (running) "\u23F8" else "\u25B6"
        tick(now)
    }

    fun tick(now: Double) {
        val it = item ?: return
        if (it.str("mode") == "timer") {
            val rem = (TimeMath.remaining(it, now) ?: 0.0).coerceAtLeast(0.0)
            val total = it.dbl("target_seconds") ?: 1.0
            ring.mode = "timer"
            ring.elapsed = (1 - rem / total).coerceIn(0.0, 1.0)
            ring.running = it.bool("running") && rem > 0
            ring.finished = rem <= 0
            ring.urgency = if (ring.running && rem <= 10 && total >= 30) ((10 - rem) / 10).toFloat().coerceIn(0f, 1f) else 0f
            ring.secFrac = rem % 1.0
            ring.orbit = TimeMath.elapsed(it, now) % 60.0
            ring.roll.set(TimeMath.hmsCeil(rem))
        } else {
            val el = TimeMath.elapsed(it, now)
            ring.mode = "stopwatch"
            ring.running = it.bool("running")
            ring.finished = false
            ring.secPos = el % 60.0
            ring.urgency = 0f
            ring.roll.set(TimeMath.hms(el.toLong()))
        }
        ring.invalidate()
    }
}
