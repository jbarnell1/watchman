package app.watchman.android.core

/** What the phone is running on right now. */
data class Power(val plugged: Boolean, val batteryPct: Int)

/**
 * How much work the phone does for its battery state (ADR-023). Plugged in: the policy as written.
 * On battery: whole-screen passes half as often. Under [LOW_PCT]: a quarter as often and the
 * on-device detector stays off; the classifier still gates every image and the ask band sends the
 * doubt to the server, so coverage does not drop, only the false-blur rate until charged.
 */
object PowerBudget {
    const val LOW_PCT = 20

    fun intervalMultiplier(p: Power): Int = when {
        p.plugged -> 1
        p.batteryPct < LOW_PCT -> 4
        else -> 2
    }

    fun detectorAllowed(p: Power): Boolean = p.plugged || p.batteryPct >= LOW_PCT

    fun describe(p: Power): String = when {
        p.plugged -> "plugged in"
        p.batteryPct < LOW_PCT -> "low battery: whole screen every ${intervalMultiplier(p)}x, detector off"
        else -> "on battery: whole screen every ${intervalMultiplier(p)}x"
    }
}
