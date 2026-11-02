package app.watchman.android.core

/**
 * The image hash from docs/api-contract.md: grayscale, area-average to 9 by 8, round to integers,
 * one bit per horizontal neighbour pair. Must reproduce models/test-vectors/dhash/vectors.json.
 * Pure Kotlin so it runs in JVM unit tests; the Bitmap wrapper lives in Pixels.kt.
 */
object DHash {
    private const val W = 9
    private const val H = 8

    /**
     * A bit is decisive when its two cells differ by at least [DECISIVE_MARGIN] grey levels. A flat
     * crop (an icon on a plain background) has few, so its hash sits near the hashes of many
     * unrelated flat crops and may only match exactly (ADR-035). Same numbers as the server.
     */
    const val DECISIVE_MARGIN = 3.0
    const val MIN_DECISIVE_BITS = 40

    data class Hashed(val hash: String, val decisive: Int) {
        val reliable: Boolean get() = decisive >= MIN_DECISIVE_BITS
    }

    fun decisive(small: DoubleArray): Int {
        var n = 0
        for (row in 0 until H) for (col in 0 until W - 1) {
            if (Math.abs(small[row * W + col] - small[row * W + col + 1]) >= DECISIVE_MARGIN) n++
        }
        return n
    }

    fun computeWithDetail(argb: IntArray, width: Int, height: Int): Hashed {
        val small = areaAverage(gray(argb), width, height)
        return Hashed(fromCells(small), decisive(small))
    }

    private fun gray(argb: IntArray): DoubleArray = DoubleArray(argb.size) { i ->
        val p = argb[i]
        0.299 * ((p shr 16) and 0xFF) + 0.587 * ((p shr 8) and 0xFF) + 0.114 * (p and 0xFF)
    }

    /** argb is width*height packed ARGB ints as Bitmap.getPixels gives them. */
    fun compute(argb: IntArray, width: Int, height: Int): String {
        val gray = DoubleArray(width * height)
        for (i in gray.indices) {
            val p = argb[i]
            val r = (p shr 16) and 0xFF
            val g = (p shr 8) and 0xFF
            val b = p and 0xFF
            gray[i] = 0.299 * r + 0.587 * g + 0.114 * b
        }
        return fromCells(areaAverage(gray, width, height))
    }

    fun areaAverage(gray: DoubleArray, inW: Int, inH: Int): DoubleArray {
        val out = DoubleArray(W * H)
        for (oy in 0 until H) {
            val y0 = oy * inH.toDouble() / H
            val y1 = (oy + 1) * inH.toDouble() / H
            for (ox in 0 until W) {
                val x0 = ox * inW.toDouble() / W
                val x1 = (ox + 1) * inW.toDouble() / W
                var total = 0.0
                var area = 0.0
                var y = Math.floor(y0).toInt()
                while (y < minOf(inH, Math.ceil(y1).toInt())) {
                    val cy = minOf(y + 1.0, y1) - maxOf(y.toDouble(), y0)
                    if (cy > 0) {
                        var x = Math.floor(x0).toInt()
                        while (x < minOf(inW, Math.ceil(x1).toInt())) {
                            val cx = minOf(x + 1.0, x1) - maxOf(x.toDouble(), x0)
                            if (cx > 0) {
                                total += gray[y * inW + x] * cx * cy
                                area += cx * cy
                            }
                            x++
                        }
                    }
                    y++
                }
                out[oy * W + ox] = total / area
            }
        }
        return out
    }

    fun fromCells(small: DoubleArray): String {
        val cells = IntArray(small.size) { Math.floor(small[it] + 0.5).toInt() }
        var bits = 0L
        for (row in 0 until H) for (col in 0 until W - 1) {
            if (cells[row * W + col] > cells[row * W + col + 1]) bits = bits or (1L shl (63 - (row * 8 + col)))
        }
        return java.lang.Long.toHexString(bits).padStart(16, '0')
    }

    fun hamming(a: String, b: String): Int =
        java.lang.Long.bitCount(java.lang.Long.parseUnsignedLong(a, 16) xor java.lang.Long.parseUnsignedLong(b, 16))

    fun matches(a: String, b: String, tolerance: Int = 6): Boolean = hamming(a, b) <= tolerance
}
