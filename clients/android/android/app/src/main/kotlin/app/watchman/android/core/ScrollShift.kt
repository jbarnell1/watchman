package app.watchman.android.core

/**
 * Scroll distances from accessibility events (ADR-032). Android reports -1 when an app did not say
 * how far it scrolled; a real one-pixel step is too small to matter, so -1 counts as unknown.
 */
object ScrollShift {
    const val UNKNOWN = -1

    fun known(dx: Int, dy: Int): Boolean = (dx != UNKNOWN && dx != 0) || (dy != UNKNOWN && dy != 0)

    fun of(delta: Int): Int = if (delta == UNKNOWN) 0 else delta
}
