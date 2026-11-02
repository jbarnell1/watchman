package app.watchman.android.core

import android.graphics.Bitmap
import android.graphics.Rect

/** [ask] is set when the device covered first and the server's answer is still to come (ADR-022). */
data class Finding(val region: Rect, val decision: Decision, val score: Double, val hash: String, val labels: List<String>,
                   val jpeg: ByteArray?, val ask: Boolean = false)

/**
 * Judge each image region of a frame at its own size (ADR-002): hash, look in the cache and the
 * known-bad list, score small crops with the classifier and larger ones with the detector too.
 */
class Pipeline(private val scorer: Scorer, private val cache: DecisionCache = DecisionCache(),
               private val metrics: Metrics = Metrics()) {
    var imagesScored = 0
        private set
    /** Running average of one classifier call, the number the status page shows. */
    var avgClassifyMs = 0.0
        private set

    fun judge(frame: Bitmap, regions: List<Rect>, thresholds: Thresholds, knownBad: List<String>,
              explicitLabels: List<String>? = null, coverOnDevice: Boolean = true, askServer: Boolean = true,
              useDetector: Boolean = true): List<Finding> {
        val out = ArrayList<Finding>()
        for (r in regions) {
            if (Decide.isTiny(r.width(), r.height())) { metrics.count("regions_tiny"); continue }
            metrics.count("regions")
            val crop = Pixels.crop(frame, r)
            val hash = metrics.timed("hash_ms") { Pixels.dhash(crop) }
            if (Decide.isKnownBad(hash, knownBad)) {
                metrics.count("known_bad")
                out.add(Finding(r, Decide.settle(Decision.COVER, coverOnDevice), 1.0, hash, listOf("known_bad"), Pixels.jpeg(crop)))
                continue
            }
            val cached = metrics.timed("cache_lookup_ms") { cache.lookup(hash) }
            if (cached != null) {
                metrics.count("cache_hits")
                if (cached.decision != Decision.SAFE) out.add(Finding(r, cached.decision, cached.score, hash, emptyList(), null))
                continue
            }
            imagesScored++
            val t0 = System.nanoTime()
            val classifier = scorer.classify(crop)
            val ms = (System.nanoTime() - t0) / 1_000_000.0
            metrics.time("classify_ms", ms)
            avgClassifyMs = if (imagesScored == 1) ms else avgClassifyMs * 0.9 + ms * 0.1
            var detectorScore: Double? = null
            var labels = emptyList<String>()
            if (useDetector && maxOf(r.width(), r.height()) >= Decide.SMALL_CROP && scorer.detectorOutput != null) {
                val boxes = metrics.timed("detect_ms") { scorer.detect(crop) }
                detectorScore = Yolo.explicitScore(boxes, scorer.detectorOutput!!, explicitLabels)
                labels = boxes.map { it.label }.distinct()
            }
            var decision = Decide.settle(Decide.combine(classifier, detectorScore, thresholds), coverOnDevice)
            val score = maxOf(classifier, detectorScore ?: 0.0)
            // Unsure: cover now, remember nothing yet; the server's answer resolves it (ADR-022).
            if (coverOnDevice && Decide.shouldAsk(score, thresholds, askServer)) {
                metrics.count("asks_started")
                out.add(Finding(r, Decision.COVER, score, hash, labels, Pixels.jpeg(crop), ask = true))
                continue
            }
            cache.remember(Judged(decision, hash, score))
            if (decision != Decision.SAFE) out.add(Finding(r, decision, score, hash, labels, Pixels.jpeg(crop)))
        }
        return out
    }

    /** The server's answer for a crop that was covered while unsure. */
    fun resolve(hash: String, decision: Decision, score: Double) { cache.remember(Judged(decision, hash, score)) }
}
