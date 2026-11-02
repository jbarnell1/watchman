package app.watchman.android.core

/**
 * Which accessibility nodes are pictures, so their rectangles go to the model (ADR-002, ADR-030).
 * Apps rarely say "image": YouTube reports a channel avatar in a list as a Button with a description
 * and no text. So besides image views, any leaf with a description and no text that is roughly square
 * counts. Text rows and toolbars are wide and short, which keeps them out.
 */
object RegionRule {
    const val MIN_SIDE = 20
    /** Picture-shaped: neither side more than twice the other. */
    const val MAX_ASPECT = 2.0

    fun isPicture(cls: String, hasDescription: Boolean, hasText: Boolean, childCount: Int, editable: Boolean,
                  width: Int, height: Int): Boolean {
        if (width < MIN_SIDE || height < MIN_SIDE) return false
        if (cls.endsWith("ImageView") || cls.endsWith("ImageButton")) return true
        if (childCount != 0 || hasText || editable || !hasDescription) return false
        val aspect = maxOf(width, height).toDouble() / minOf(width, height)
        return aspect <= MAX_ASPECT
    }
}
