package app.watchman.android.core

import kotlin.math.abs
import kotlin.math.sign

/**
 * Raw touchscreen input for blurs that follow a finger (ADR-044). Pure Kotlin: the shell-side
 * reader and the app share these, and JVM unit tests run them.
 */
object Touch {
    const val DOWN = 0
    const val MOVE = 1
    const val UP = 2
    /** A second finger came down: a pinch or a two-finger gesture, not a scroll. */
    const val MULTI = 3

    /** One touchscreen from `getevent -pl`: its device node and the largest coordinates it reports. */
    data class Device(val path: String, val maxX: Int, val maxY: Int)

    private val ADD = Regex("""^add device \d+: (\S+)""")
    private val POSITION = Regex("""ABS_MT_POSITION_([XY])\s*:.*\bmax (\d+)""")

    /** The touchscreen among `getevent -pl` output: the first device with multi-touch positions. */
    fun findDevice(getevent: String): Device? {
        var path: String? = null
        var maxX = -1
        var maxY = -1
        for (line in getevent.lines()) {
            val add = ADD.find(line)
            if (add != null) {
                if (path != null && maxX > 0 && maxY > 0) return Device(path, maxX, maxY)
                path = add.groupValues[1]; maxX = -1; maxY = -1
                continue
            }
            val pos = POSITION.find(line) ?: continue
            if (pos.groupValues[1] == "X") maxX = pos.groupValues[2].toInt() else maxY = pos.groupValues[2].toInt()
        }
        return if (path != null && maxX > 0 && maxY > 0) Device(path, maxX, maxY) else null
    }
}

/**
 * Turns Linux multi-touch (protocol B) input events into one finger's down, move and up. Feed it
 * every event; at each frame of input (SYN_REPORT) it answers with a sample, or null when nothing
 * that matters changed.
 */
class TouchDecoder {
    data class Sample(val kind: Int, val x: Int, val y: Int, val tMs: Long)

    private val ids = IntArray(SLOTS) { -1 }
    private val xs = IntArray(SLOTS)
    private val ys = IntArray(SLOTS)
    private var slot = 0
    private var down = false
    private var multi = false
    private var lastX = -1
    private var lastY = -1

    fun feed(type: Int, code: Int, value: Int, tMs: Long): Sample? {
        if (type == EV_ABS) {
            when (code) {
                ABS_MT_SLOT -> slot = value.coerceIn(0, SLOTS - 1)
                ABS_MT_TRACKING_ID -> ids[slot] = value
                ABS_MT_POSITION_X -> xs[slot] = value
                ABS_MT_POSITION_Y -> ys[slot] = value
            }
            return null
        }
        if (type != EV_SYN || code != SYN_REPORT) return null
        val active = ids.count { it >= 0 }
        if (active == 0) {
            val was = down
            down = false; multi = false
            return if (was) Sample(Touch.UP, lastX, lastY, tMs) else null
        }
        if (active > 1) {
            if (multi) return null
            multi = true
            return if (down) Sample(Touch.MULTI, lastX, lastY, tMs) else null
        }
        if (multi) return null  // the gesture stays a pinch until every finger lifts
        val i = ids.indexOfFirst { it >= 0 }
        val x = xs[i]
        val y = ys[i]
        if (!down) { down = true; lastX = x; lastY = y; return Sample(Touch.DOWN, x, y, tMs) }
        if (x == lastX && y == lastY) return null
        lastX = x; lastY = y
        return Sample(Touch.MOVE, x, y, tMs)
    }

    companion object {
        const val EV_SYN = 0
        const val EV_ABS = 3
        const val SYN_REPORT = 0
        const val ABS_MT_SLOT = 0x2f
        const val ABS_MT_POSITION_X = 0x35
        const val ABS_MT_POSITION_Y = 0x36
        const val ABS_MT_TRACKING_ID = 0x39
        const val SLOTS = 10
    }
}

/**
 * Moves blurs with the finger along one axis (ADR-044). While a finger drags a list, the content
 * moves with it one to one once the drag passes the touch slop, so the blur can move on every touch
 * sample instead of every 100 ms. Android's scroll events still arrive and correct the drift (a
 * header that collapses, the end of a list); drift smaller than the lag of a fast drag is left
 * alone and the rest is applied. A drag that brings no scroll event at all (a swipe on something
 * that does not scroll) is undone. Distances are scroll units: positive means content moved up or
 * left, as in accessibility scroll events.
 */
class FingerScroll(private val slopPx: Int) {
    /** The finger lifted: apply [correction] now, then fling at [velocity] units per ms; [pending] is applied since the last event. */
    data class Lift(val correction: Int, val velocity: Double, val pending: Double)

    var following = false
        private set
    /** Past the slop: the content is moving. */
    var dragging = false
        private set
    private var confirmed = false
    private var startPos = 0
    private var lastPos = 0
    private var startedAt = 0L
    private var appliedSinceEvent = 0
    private var appliedTotal = 0
    private var drift = 0
    private val times = LongArray(HISTORY)
    private val positions = IntArray(HISTORY)
    private var count = 0

    /** A finger touched down inside the list that last scrolled; [pos] is along the scroll axis. */
    fun down(pos: Int, tMs: Long) {
        following = true; dragging = false; confirmed = false
        startPos = pos; lastPos = pos; startedAt = tMs
        appliedSinceEvent = 0; appliedTotal = 0; drift = 0; count = 0
        record(pos, tMs)
    }

    /** The finger moved to [pos]: returns how far to move the blurs now. */
    fun move(pos: Int, tMs: Long): Int {
        if (!following) return 0
        record(pos, tMs)
        val step: Int
        if (!dragging) {
            val travel = startPos - pos
            if (abs(travel) <= slopPx) return 0
            // Lists start dragging past the slop and leave the slop out of the first step.
            dragging = true
            startedAt = tMs
            step = travel - slopPx * travel.sign
        } else {
            step = lastPos - pos
        }
        lastPos = pos
        appliedSinceEvent += step
        appliedTotal += step
        return step
    }

    /** A real scroll event of [delta] units during the drag: returns the correction to apply now. */
    fun event(delta: Int, tMs: Long): Int {
        confirmed = true
        drift += delta - appliedSinceEvent
        appliedSinceEvent = 0
        val allowance = (abs(velocity(tMs)) * LAG_MS).toInt()
        if (abs(drift) <= allowance) return 0
        val correction = drift - allowance * drift.sign
        drift -= correction
        appliedTotal += correction
        return correction
    }

    /**
     * Run a little after the drag starts: a drag that no scroll event confirmed moved nothing, so
     * the blurs go back. Returns how far to move them.
     */
    fun check(tMs: Long): Int {
        if (!following || !dragging || confirmed || tMs - startedAt < CONFIRM_MS) return 0
        following = false
        return -appliedTotal
    }

    /** The finger lifted, or a second finger came down ([fling] false). */
    fun up(tMs: Long, fling: Boolean = true): Lift {
        val was = following
        following = false
        if (!was || !dragging) return Lift(0, 0.0, 0.0)
        if (!confirmed) return Lift(-appliedTotal, 0.0, 0.0)
        val v = if (fling) velocity(tMs) else 0.0
        return Lift(drift, v, appliedSinceEvent.toDouble())
    }

    /** Content speed over the last [VELOCITY_WINDOW_MS] of samples, units per ms. */
    fun velocity(tMs: Long): Double {
        val n = minOf(count, HISTORY)
        if (n < 2) return 0.0
        val newest = (count - 1) % HISTORY
        if (tMs - times[newest] > VELOCITY_WINDOW_MS) return 0.0
        var oldest = newest
        for (k in 1 until n) {
            val i = (count - 1 - k) % HISTORY
            if (times[newest] - times[i] > VELOCITY_WINDOW_MS) break
            oldest = i
        }
        val dt = times[newest] - times[oldest]
        if (dt <= 0) return 0.0
        return (positions[oldest] - positions[newest]).toDouble() / dt
    }

    private fun record(pos: Int, tMs: Long) {
        val i = count % HISTORY
        times[i] = tMs; positions[i] = pos; count++
    }

    companion object {
        /** A drag with no scroll event this long after it started moved nothing. */
        const val CONFIRM_MS = 250L
        /** Scroll events trail the finger; drift under this much travel is timing, not error. */
        const val LAG_MS = 50.0
        const val VELOCITY_WINDOW_MS = 80L
        const val HISTORY = 32
    }
}
