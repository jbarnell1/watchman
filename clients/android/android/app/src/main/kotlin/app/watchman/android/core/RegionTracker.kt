package app.watchman.android.core

/**
 * Pictures on screen as tracks over time (ADR-038). A track is one accessibility node at one place,
 * keyed by its view id or class and its bounds. Two kinds of track get judged less often than every
 * pass, both at the video cadence:
 *  - a video surface: its frames change constantly, so it is judged once per [cadenceMs];
 *  - a live picture: one whose fingerprint changed on [LIVE_CHANGES] of its last [WINDOW] judgements,
 *    such as a button drawn over a playing video.
 * Pictures that lie inside a video surface are not judged on their own: the video's judgement
 * covers their pixels. A held track keeps its patch until its next judgement.
 * Pure Kotlin with plain coordinates so it runs in JVM unit tests.
 */
class RegionTracker(private val now: () -> Long = System::currentTimeMillis) {
    data class Region(val key: String, val left: Int, val top: Int, val right: Int, val bottom: Int, val video: Boolean) {
        val area: Long get() = (right - left).toLong() * (bottom - top)
    }

    /** What a pass should judge now, and what it keeps covered without judging. */
    data class Plan(val judge: List<Region>, val held: List<Region>, val insideVideo: Int)

    private class Track(var lastHash: String? = null, var judgedAt: Long = 0L, var seenAt: Long = 0L,
                        val changes: ArrayDeque<Boolean> = ArrayDeque())

    private val tracks = HashMap<String, Track>()

    fun plan(regions: List<Region>, cadenceMs: Long): Plan {
        val t = now()
        val videos = regions.filter { it.video }
        val judge = ArrayList<Region>()
        val held = ArrayList<Region>()
        var inside = 0
        for (r in regions) {
            if (!r.video && videos.any { v -> v !== r && insideShare(r, v) >= INSIDE }) { inside++; continue }
            val track = tracks.getOrPut(r.key) { Track() }
            track.seenAt = t
            val due = track.judgedAt == 0L || t - track.judgedAt >= cadenceMs
            if ((r.video || isLive(track)) && !due) held.add(r) else judge.add(r)
        }
        tracks.entries.removeIf { t - it.value.seenAt > FORGET_MS }
        return Plan(judge, held, inside)
    }

    /** A judged track's fingerprint, to tell live pictures from still ones. */
    fun judged(key: String, hash: String) {
        val track = tracks[key] ?: return
        val previous = track.lastHash
        if (previous != null) {
            track.changes.addLast(!DHash.matches(previous, hash))
            while (track.changes.size > WINDOW) track.changes.removeFirst()
        }
        track.lastHash = hash
        track.judgedAt = now()
    }

    fun isLive(key: String): Boolean = tracks[key]?.let { isLive(it) } ?: false

    private fun isLive(track: Track): Boolean = track.changes.count { it } >= LIVE_CHANGES

    companion object {
        const val WINDOW = 4
        const val LIVE_CHANGES = 3
        /** Share of a picture's area inside a video surface for the picture to count as part of it. */
        const val INSIDE = 0.9
        const val FORGET_MS = 60_000L

        fun insideShare(a: Region, b: Region): Double {
            val w = minOf(a.right, b.right) - maxOf(a.left, b.left)
            val h = minOf(a.bottom, b.bottom) - maxOf(a.top, b.top)
            if (w <= 0 || h <= 0 || a.area <= 0) return 0.0
            return w.toLong() * h / a.area.toDouble()
        }
    }
}
