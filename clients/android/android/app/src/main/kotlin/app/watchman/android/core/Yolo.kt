package app.watchman.android.core

data class Box(val label: String, val score: Double, val x: Double, val y: Double, val w: Double, val h: Double)

data class LetterboxInfo(val scale: Double, val padX: Int, val padY: Int)

/** YOLOv8 output decoding with NMS, matching models/watchman_models/runner.py. Pure Kotlin. */
object Yolo {
    fun decode(raw: FloatArray, dims: IntArray, output: ModelOutput, lb: LetterboxInfo?, conf: Double = 0.25, nmsIou: Double = 0.45): List<Box> {
        val classes = output.labels.size
        val transposed: Boolean
        val rows: Int
        val anchors: Int
        if (dims.size == 3 && dims[1] == 4 + classes) { rows = dims[1]; anchors = dims[2]; transposed = false }
        else if (dims.size == 3 && dims[2] == 4 + classes) { rows = dims[2]; anchors = dims[1]; transposed = true }
        else throw IllegalArgumentException("unexpected output shape ${dims.joinToString("x")}")
        fun at(r: Int, a: Int): Double = (if (transposed) raw[a * rows + r] else raw[r * anchors + a]).toDouble()

        val boxes = ArrayList<Box>()
        for (a in 0 until anchors) {
            var best = 0
            var bestScore = Double.NEGATIVE_INFINITY
            for (c in 0 until classes) { val s = at(4 + c, a); if (s > bestScore) { bestScore = s; best = c } }
            if (bestScore < conf) continue
            var x = at(0, a) - at(2, a) / 2
            var y = at(1, a) - at(3, a) / 2
            var w = at(2, a)
            var h = at(3, a)
            if (lb != null) { x = (x - lb.padX) / lb.scale; y = (y - lb.padY) / lb.scale; w /= lb.scale; h /= lb.scale }
            boxes.add(Box(output.labels[best], bestScore, x, y, w, h))
        }
        val kept = ArrayList<Box>()
        for (label in boxes.map { it.label }.distinct()) {
            var same = boxes.filter { it.label == label }.sortedByDescending { it.score }
            while (same.isNotEmpty()) {
                val top = same.first()
                kept.add(top)
                same = same.drop(1).filter { iou(top, it) < nmsIou }
            }
        }
        return kept.sortedByDescending { it.score }
    }

    fun iou(a: Box, b: Box): Double {
        val iw = maxOf(0.0, minOf(a.x + a.w, b.x + b.w) - maxOf(a.x, b.x))
        val ih = maxOf(0.0, minOf(a.y + a.h, b.y + b.h) - maxOf(a.y, b.y))
        val inter = iw * ih
        val union = a.w * a.h + b.w * b.h - inter
        return if (union > 0) inter / union else 0.0
    }

    fun explicitScore(boxes: List<Box>, output: ModelOutput, override: List<String>? = null): Double {
        val explicit = (override ?: output.explicitLabels ?: emptyList()).toHashSet()
        return boxes.filter { it.label in explicit }.maxOfOrNull { it.score } ?: 0.0
    }

    /** Normalised NCHW tensor from packed ARGB pixels of a size-by-size square. */
    fun tensor(argb: IntArray, size: Int, input: ModelInput): FloatArray {
        val n = size * size
        val out = FloatArray(3 * n)
        for (i in 0 until n) {
            val p = argb[i]
            val rgb = intArrayOf((p shr 16) and 0xFF, (p shr 8) and 0xFF, p and 0xFF)
            val order = if (input.color == "BGR") intArrayOf(2, 1, 0) else intArrayOf(0, 1, 2)
            for (c in 0 until 3) {
                val v = ((rgb[order[c]] / input.scale - input.mean[c]) / input.std[c]).toFloat()
                if (input.layout == "NCHW" || input.layout == "CHW") out[c * n + i] = v else out[i * 3 + c] = v
            }
        }
        return out
    }

    /** Probability of the positive class, or the summed probability of several (ADR-034). */
    fun softmaxPositive(logits: FloatArray, output: ModelOutput): Double {
        val names = output.positives ?: listOf(output.positive ?: output.labels[0])
        val idx = names.map { output.labels.indexOf(it) }.filter { it >= 0 }
        if (output.kind == "sigmoid") return idx.maxOf { 1.0 / (1.0 + Math.exp(-logits[it].toDouble())) }
        val max = logits.max()
        val exps = logits.map { Math.exp((it - max).toDouble()) }
        return idx.sumOf { exps[it] } / exps.sum()
    }
}
