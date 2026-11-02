package app.watchman.android.core

import android.graphics.Rect

/**
 * The share of a frame that covered pictures occupy, counting overlaps once (ADR-036). Rectangles
 * are given as (left, top, right, bottom) so this runs in JVM unit tests; a [CELLS] by [CELLS] grid
 * is exact enough for a 40% decision.
 */
object Coverage {
    const val CELLS = 64
    /** Above this share of the screen, sure covers become the whole-screen block. */
    const val BLOCK_SHARE = 0.4

    fun share(rects: List<IntArray>, width: Int, height: Int): Double {
        if (width <= 0 || height <= 0 || rects.isEmpty()) return 0.0
        val grid = BooleanArray(CELLS * CELLS)
        for (r in rects) {
            val x0 = (r[0].coerceIn(0, width) * CELLS) / width
            val x1 = ((r[2].coerceIn(0, width) * CELLS) + width - 1) / width
            val y0 = (r[1].coerceIn(0, height) * CELLS) / height
            val y1 = ((r[3].coerceIn(0, height) * CELLS) + height - 1) / height
            for (y in y0 until y1) for (x in x0 until x1) grid[y * CELLS + x] = true
        }
        return grid.count { it }.toDouble() / grid.size
    }

    fun shareOfRects(rects: List<Rect>, width: Int, height: Int): Double =
        share(rects.map { intArrayOf(it.left, it.top, it.right, it.bottom) }, width, height)
}
