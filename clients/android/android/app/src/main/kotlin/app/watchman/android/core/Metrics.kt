package app.watchman.android.core

import android.util.Log
import java.io.File

/**
 * Work metrics (ADR-025): counters and timing histograms. Buckets hold samples at or below each
 * bound in [BOUNDS_MS], the last everything slower, so windows merge by adding and the server can
 * sum a day of heartbeats. Counts and milliseconds only, never content.
 */
class Timing(var n: Long = 0, var sum: Double = 0.0, var max: Double = 0.0, val buckets: LongArray = LongArray(Metrics.BUCKETS)) {
    fun add(ms: Double) {
        n++; sum += ms
        if (ms > max) max = ms
        buckets[Metrics.bucketOf(ms)]++
    }

    fun merge(o: Timing) {
        n += o.n; sum += o.sum; max = maxOf(max, o.max)
        for (i in buckets.indices) buckets[i] += o.buckets[i]
    }

    /** The bucket bound at or below which a share q of the samples fall, capped at the max. */
    fun percentile(q: Double): Double {
        if (n == 0L) return 0.0
        val want = q * n
        var seen = 0L
        for (i in buckets.indices) {
            seen += buckets[i]
            if (seen >= want) return minOf(if (i < Metrics.BOUNDS_MS.size) Metrics.BOUNDS_MS[i] else max, max)
        }
        return max
    }

    fun toMap(): Map<String, Any> = mapOf("n" to n, "sum" to Math.round(sum * 10) / 10.0, "max" to Math.round(max * 10) / 10.0,
        "buckets" to buckets.toList())
}

class MetricsSnapshot(var windowS: Long = 0, val counters: MutableMap<String, Long> = HashMap(), val timings: MutableMap<String, Timing> = HashMap()) {
    val isEmpty: Boolean get() = counters.isEmpty() && timings.isEmpty()

    fun merge(o: MetricsSnapshot) {
        windowS += o.windowS
        for ((k, v) in o.counters) counters[k] = (counters[k] ?: 0) + v
        for ((k, t) in o.timings) timings.getOrPut(k) { Timing() }.merge(t)
    }

    /** The heartbeat's `metrics` field (docs/api-contract.md). */
    fun toMap(): Map<String, Any> = mapOf("window_s" to windowS, "counters" to counters.toSortedMap(),
        "timings" to timings.toSortedMap().mapValues { it.value.toMap() })
}

/**
 * Two windows over the same tallies: a minute for the local log and Logcat, and the time since the
 * last heartbeat for the server. Thread-safe; recording costs a lock and an array increment.
 */
class Metrics(private val now: () -> Long = { System.currentTimeMillis() }) {
    companion object {
        val BOUNDS_MS = doubleArrayOf(1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0, 1024.0)
        const val BUCKETS = 12
        fun bucketOf(ms: Double): Int { for (i in BOUNDS_MS.indices) if (ms <= BOUNDS_MS[i]) return i; return BOUNDS_MS.size }
        /** The one instance the app's services share. */
        val shared = Metrics()
    }

    private var minute = MetricsSnapshot()
    private var minuteSince = now()
    private var beat = MetricsSnapshot()
    private var beatSince = now()

    @Synchronized fun count(name: String, n: Long = 1) {
        if (n == 0L) return
        minute.counters[name] = (minute.counters[name] ?: 0) + n
        beat.counters[name] = (beat.counters[name] ?: 0) + n
    }

    @Synchronized fun time(name: String, ms: Double) {
        minute.timings.getOrPut(name) { Timing() }.add(ms)
        beat.timings.getOrPut(name) { Timing() }.add(ms)
    }

    inline fun <T> timed(name: String, block: () -> T): T {
        val t0 = System.nanoTime()
        try { return block() } finally { time(name, (System.nanoTime() - t0) / 1_000_000.0) }
    }

    @Synchronized fun takeMinute(): MetricsSnapshot {
        val m = minute; m.windowS = (now() - minuteSince) / 1000
        minute = MetricsSnapshot(); minuteSince = now()
        return m
    }

    @Synchronized fun takeHeartbeat(): MetricsSnapshot {
        val m = beat; m.windowS = (now() - beatSince) / 1000
        beat = MetricsSnapshot(); beatSince = now()
        return m
    }

    /** A heartbeat that did not reach the server gives its window back. */
    @Synchronized fun giveBack(m: MetricsSnapshot) { beat.merge(m) }
}

/** JSON lines with one rotation at [maxBytes]; never throws, a log must not stop protection. */
class MetricsLog(private val file: File, private val maxBytes: Long = 5L * 1024 * 1024) {
    @Synchronized fun append(record: Map<String, Any?>) {
        try {
            file.parentFile?.mkdirs()
            val line = gson.toJson(record) + "\n"
            if (file.exists() && file.length() + line.length > maxBytes) file.renameTo(File(file.path + ".1"))
            file.appendText(line)
            Log.i("WatchmanMetrics", line.trim())
        } catch (_: Exception) { }
    }
}
