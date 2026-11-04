package app.watchman.android.core

/**
 * Fail closed and loud: a stretch with no protection is reported after the fact. The watcher
 * service writes `alive_at` every minute; on its next start a silence longer than [THRESHOLD_MS]
 * becomes a `protection_gap` event. Pure, so the decision is tested with a fake clock.
 */
object ProtectionGap {
    const val THRESHOLD_MS = 2 * 60 * 1000L
    const val ALIVE_EVERY_MS = 60 * 1000L

    data class Gap(val fromMillis: Long, val toMillis: Long, val reason: String)

    /**
     * [aliveAt] is the last time the service said it was alive (null: never ran). [cleanStop] is set
     * only by a stop that already reported itself. [bootedAt] is when the phone last started.
     * [pausedUntil] is the end of a partner-code pause, if one was in force: time inside it is not a
     * gap. A clean stop inside a pause is quiet only until the pause ends; what follows it is a gap.
     */
    fun decide(aliveAt: Long?, cleanStop: Boolean, now: Long, bootedAt: Long, pausedUntil: Long? = null): Gap? {
        if (aliveAt == null) return null
        if (cleanStop && (pausedUntil == null || pausedUntil <= aliveAt)) return null
        val from = if (pausedUntil != null && pausedUntil > aliveAt) pausedUntil else aliveAt
        if (now - from <= THRESHOLD_MS) return null
        val reason = if (bootedAt > aliveAt) "reboot" else "killed"
        return Gap(from, now, reason)
    }

    fun event(gap: Gap): WatchmanEvent = WatchmanEvent(
        type = "protection_gap", occurredAt = Iso.format(gap.toMillis), action = "none",
        detail = mapOf("from" to Iso.format(gap.fromMillis), "to" to Iso.format(gap.toMillis), "reason" to gap.reason),
    )
}

/** The heartbeat's `judge` field: how much screen time there was and how often a model looked at it. */
class JudgeWindow(var windowS: Long = 0, var screenOnS: Long = 0, var unwatchedS: Long = 0, var judged: Long = 0,
                  var lastJudgedAt: Long? = null) {
    fun toMap(): Map<String, Any?> = mapOf("window_s" to windowS, "screen_on_s" to screenOnS, "unwatched_s" to unwatchedS,
        "judged" to judged, "last_judged_at" to lastJudgedAt?.let { Iso.format(it) })
}

/**
 * Screen-on time and model runs since the last delivered heartbeat. `unwatched_s` is the part of
 * the screen-on time when nothing was meant to be judged: a partner-code pause, an exempt app in
 * front, or a screenshot-blocking window. A heartbeat that fails gives its window back, like
 * [Metrics]. Thread-safe.
 */
class JudgeTally(private val now: () -> Long = { System.currentTimeMillis() }) {
    companion object {
        const val PAUSED = "paused"
        const val EXEMPT = "exempt"
        const val SECURE = "secure"
        val shared = JudgeTally()
    }

    private var since = now()
    private var lastT = since
    private var on = false
    private val unwatched = HashSet<String>()
    private var onMs = 0L
    private var unwatchedMs = 0L
    private var judged = 0L
    private var lastJudgedAt: Long? = null

    /** Adds the time since the last change to the tallies the current state earns. */
    private fun advance(t: Long) {
        val dt = maxOf(0, t - lastT)
        if (on) { onMs += dt; if (unwatched.isNotEmpty()) unwatchedMs += dt }
        lastT = t
    }

    @Synchronized fun screenOn() { advance(now()); on = true }

    @Synchronized fun screenOff() { advance(now()); on = false }

    /** [reason] ([PAUSED], [EXEMPT] or [SECURE]) starts or stops holding judging off. */
    @Synchronized fun unwatched(reason: String, active: Boolean) {
        advance(now())
        if (active) unwatched.add(reason) else unwatched.remove(reason)
    }

    @Synchronized fun judged() { judged++; lastJudgedAt = now() }

    @Synchronized fun take(): JudgeWindow {
        val t = now()
        advance(t)
        val w = JudgeWindow((t - since) / 1000, onMs / 1000, unwatchedMs / 1000, judged, lastJudgedAt)
        since = t; onMs = 0; unwatchedMs = 0; judged = 0
        return w
    }

    @Synchronized fun giveBack(w: JudgeWindow) {
        since -= w.windowS * 1000
        onMs += w.screenOnS * 1000
        unwatchedMs += w.unwatchedS * 1000
        judged += w.judged
        if (lastJudgedAt == null) lastJudgedAt = w.lastJudgedAt
    }
}

/**
 * Seconds per package that a screenshot-blocking (FLAG_SECURE) window was in front, for the
 * heartbeat's `secure_apps`. Such windows are reported, never covered: banking apps need them.
 * Each failed screenshot extends the current stretch; a frame that arrives, an app switch or the
 * screen going off ends it. A stretch nobody confirms for [STALE_MS] counts only that long.
 */
class SecureApps(private val now: () -> Long = { System.currentTimeMillis() },
                 private val onSecure: (Boolean) -> Unit = {}) {
    companion object {
        const val MAX_PACKAGES = 64
        const val STALE_MS = 2 * 60 * 1000L
        /** A secure window in front also counts as unwatched screen time. */
        val shared = SecureApps(onSecure = { JudgeTally.shared.unwatched(JudgeTally.SECURE, it) })
    }

    private val ms = HashMap<String, Long>()
    private var current: String? = null
    private var since = 0L
    private var lastSeen = 0L

    /** A screenshot was refused because [pkg]'s window is secure. */
    @Synchronized fun seen(pkg: String) {
        if (pkg.isEmpty()) return
        val t = now()
        if (current == null) onSecure(true)
        if (current != pkg) { close(t); current = pkg; since = t }
        lastSeen = t
    }

    /** A frame arrived, the app changed, or the screen went off. */
    @Synchronized fun clear() {
        if (current == null) return
        close(now())
        onSecure(false)
    }

    private fun close(t: Long) {
        val pkg = current ?: return
        val end = minOf(t, lastSeen + STALE_MS)
        if (end > since) ms[pkg] = (ms[pkg] ?: 0) + (end - since)
        current = null
    }

    /** At most [MAX_PACKAGES] packages, longest first, in whole seconds; the rest wait for the next heartbeat. */
    @Synchronized fun take(): Map<String, Long> {
        val t = now()
        val open = current
        if (open != null) {
            val keepSeen = lastSeen
            close(t)
            if (t - keepSeen < STALE_MS) { current = open; since = t; lastSeen = keepSeen } else onSecure(false)
        }
        val sorted = ms.entries.filter { it.value >= 1000 }.sortedByDescending { it.value }
        val out = LinkedHashMap<String, Long>()
        for (e in sorted.take(MAX_PACKAGES)) { out[e.key] = e.value / 1000; ms[e.key] = e.value % 1000 }
        ms.entries.removeIf { it.value == 0L }
        return out
    }

    @Synchronized fun giveBack(seconds: Map<String, Long>) {
        for ((k, v) in seconds) ms[k] = (ms[k] ?: 0) + v * 1000
    }
}
