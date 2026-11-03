package app.watchman.android.core

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min

/**
 * Dead reckoning for blurs that follow a scroll (ADR-042). Android sends an accessibility scroll
 * event at most every 100 ms per view, with the distance scrolled since the last one. Between events
 * the blur keeps moving every frame at the last measured speed; each real event corrects the blur
 * to where the content really is; when events stop the speed eases to zero, as a fling does.
 * One instance per axis. Distances are in scroll units: positive means content moved up or left.
 * Pure Kotlin so it runs in JVM unit tests.
 */
class ScrollPredictor {
    private var velocity = 0.0          // px per ms
    private var lastEventAt = 0L
    private var lastFrameAt = 0L
    private var predictedSinceEvent = 0.0
    private var remainder = 0.0

    val active: Boolean get() = abs(velocity) > STOP_SPEED
    val speed: Double get() = abs(velocity)

    /**
     * A real scroll event of [delta] px at [tMs]. Returns how far to move the blur now: the real
     * distance minus what was already predicted since the last event.
     */
    fun onEvent(delta: Int, tMs: Long): Int {
        val gap = tMs - lastEventAt
        val measured = delta / (if (lastEventAt == 0L || gap > GESTURE_GAP_MS) EVENT_INTERVAL_MS else max(gap, 1L).toDouble())
        // Smooth a little: event gaps jitter, and one short gap would otherwise spike the speed.
        velocity = if (lastEventAt == 0L || gap > GESTURE_GAP_MS) measured else 0.6 * measured + 0.4 * velocity
        val correction = delta - predictedSinceEvent
        predictedSinceEvent = 0.0
        lastEventAt = tMs
        lastFrameAt = tMs
        return take(correction)
    }

    /** A finger came down: a fling in progress stops (ADR-044). */
    fun stop() { velocity = 0.0; predictedSinceEvent = 0.0; remainder = 0.0 }

    /**
     * The finger lifted at [velocity] px per ms (ADR-044): keep moving as a fling, with [pending]
     * px already applied since the last real event so the next event corrects only the rest.
     */
    fun handOff(velocity: Double, pending: Double, tMs: Long) {
        this.velocity = velocity
        predictedSinceEvent = pending
        lastEventAt = tMs
        lastFrameAt = tMs
    }

    /** One display frame at [tMs]. Returns how far to move the blur for this frame. */
    fun onFrame(tMs: Long): Int {
        if (!active) return 0
        val dt = min((tMs - lastFrameAt).toDouble(), MAX_FRAME_MS)
        lastFrameAt = tMs
        if (dt <= 0) return 0
        // Past the usual event gap with no event: the finger lifted or the list stopped; ease out.
        if (tMs - lastEventAt > EVENT_INTERVAL_MS + GRACE_MS) velocity *= exp(-dt / DECAY_MS)
        if (!active) { velocity = 0.0; return 0 }
        // Never run further ahead of the last real event than one more event's worth of travel.
        val step = velocity * dt
        val cap = abs(velocity) * (EVENT_INTERVAL_MS + GRACE_MS)
        if (abs(predictedSinceEvent + step) > cap) return 0
        predictedSinceEvent += step
        return take(step)
    }

    private fun take(px: Double): Int {
        val total = px + remainder
        val whole = total.toInt()
        remainder = total - whole
        return whole
    }

    companion object {
        /** Android's throttle on recurring accessibility events (ViewConfiguration). */
        const val EVENT_INTERVAL_MS = 100.0
        const val GRACE_MS = 30.0
        /** A gap longer than this starts a new gesture. */
        const val GESTURE_GAP_MS = 300L
        const val DECAY_MS = 120.0
        const val STOP_SPEED = 0.02
        const val MAX_FRAME_MS = 50.0
    }
}
