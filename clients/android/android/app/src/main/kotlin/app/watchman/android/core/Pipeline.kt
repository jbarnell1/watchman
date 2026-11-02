package app.watchman.android.core

import android.graphics.Bitmap
import android.graphics.Rect

/** [ask] is set when the device covered first and the server's answer is still to come (ADR-022). */
data class Finding(val region: Rect, val decision: Decision, val score: Double, val hash: String, val labels: List<String>,
                   val jpeg: ByteArray?, val ask: Boolean = false)

/**
 * Judge each image region of a frame at its own size (ADR-002): hash, look in the cache and the
 * known-bad list, score small crops with the classifier and larger ones with the detector too.
 *
 * Cache hits and known-bad pictures are settled first. When a sorter model is loaded the pictures
 * left are classified in the order of its suspicion, most suspicious first (ADR-034); every one is
 * still classified, so the sorter changes how soon a cover appears, never whether it does.
 * [onFinding] hears each finding the moment it is decided, so the overlay can cover it at once.
 */
class Pipeline(private val scorer: Scorer, private val cache: DecisionCache = DecisionCache(),
               private val metrics: Metrics = Metrics()) {
    var imagesScored = 0
        private set
    /** Running average of one classifier call, the number the status page shows. */
    var avgClassifyMs = 0.0
        private set

    private class Todo(val region: Rect, val crop: Bitmap, val hash: String)

    fun judge(frame: Bitmap, regions: List<Rect>, thresholds: Thresholds, knownBad: List<String>,
              explicitLabels: List<String>? = null, coverOnDevice: Boolean = true, askServer: Boolean = true,
              useDetector: Boolean = true, onFinding: ((Finding) -> Unit)? = null,
              onHashed: ((Rect, String) -> Unit)? = null): List<Finding> {
        val out = ArrayList<Finding>()
        fun emit(f: Finding) { out.add(f); onFinding?.invoke(f) }
        val todo = ArrayList<Todo>()
        for (r in regions) {
            if (Decide.isTiny(r.width(), r.height())) { metrics.count("regions_tiny"); continue }
            metrics.count("regions")
            val crop = Pixels.crop(frame, r)
            val hashed = metrics.timed("hash_ms") { Pixels.dhashWithDetail(crop) }
            val hash = hashed.hash
            onHashed?.invoke(r, hash)
            if (!hashed.reliable) metrics.count("flat_hashes")
            if (Decide.isKnownBad(hash, knownBad, hashed.reliable)) {
                metrics.count("known_bad")
                emit(Finding(r, Decide.settle(Decision.COVER, coverOnDevice), 1.0, hash, listOf("known_bad"), Pixels.jpeg(crop)))
                continue
            }
            val cached = metrics.timed("cache_lookup_ms") { cache.lookup(hash, hashed.reliable) }
            if (cached != null) {
                metrics.count("cache_hits")
                if (cached.decision != Decision.SAFE) emit(Finding(r, cached.decision, cached.score, hash, emptyList(), null))
                continue
            }
            todo.add(Todo(r, crop, hash))
        }
        val ordered = if (todo.size > 1 && scorer.hasGate) {
            val risks = todo.map { metrics.timed("gate_ms") { scorer.gate(it.crop) } }
            metrics.count("gate_sorts")
            order(risks).map { todo[it] }
        } else todo
        for (t in ordered) {
            val r = t.region
            imagesScored++
            val t0 = System.nanoTime()
            val classifier = scorer.classify(t.crop)
            val ms = (System.nanoTime() - t0) / 1_000_000.0
            metrics.time("classify_ms", ms)
            avgClassifyMs = if (imagesScored == 1) ms else avgClassifyMs * 0.9 + ms * 0.1
            var detectorScore: Double? = null
            var labels = emptyList<String>()
            if (useDetector && maxOf(r.width(), r.height()) >= Decide.SMALL_CROP && scorer.detectorOutput != null) {
                val boxes = metrics.timed("detect_ms") { scorer.detect(t.crop) }
                detectorScore = Yolo.explicitScore(boxes, scorer.detectorOutput!!, explicitLabels)
                labels = boxes.map { it.label }.distinct()
            }
            val decision = Decide.settle(Decide.combine(classifier, detectorScore, thresholds), coverOnDevice)
            val score = maxOf(classifier, detectorScore ?: 0.0)
            // Unsure: cover now, remember nothing yet; the server's answer resolves it (ADR-022).
            if (coverOnDevice && Decide.shouldAsk(score, thresholds, askServer)) {
                metrics.count("asks_started")
                emit(Finding(r, Decision.COVER, score, t.hash, labels, Pixels.jpeg(t.crop), ask = true))
                continue
            }
            cache.remember(Judged(decision, t.hash, score))
            if (decision != Decision.SAFE) emit(Finding(r, decision, score, t.hash, labels, Pixels.jpeg(t.crop)))
        }
        return out
    }

    /** The server's answer for a crop that was covered while unsure. */
    fun resolve(hash: String, decision: Decision, score: Double) { cache.remember(Judged(decision, hash, score)) }

    companion object {
        /** Indexes by the sorter's suspicion, highest first; ties keep screen order. */
        fun order(risks: List<Double>): List<Int> = risks.indices.sortedByDescending { risks[it] }
    }
}
