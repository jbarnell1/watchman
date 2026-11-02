package app.watchman.android.overlay

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.PixelFormat
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import app.watchman.android.core.Finding
import app.watchman.android.core.Pixels

/**
 * One full-screen, touch-transparent overlay window. Each covered region gets a pixelated copy of
 * its crop so the picture's colour and layout stay while detail goes. A patch stays while its picture
 * is still found (region passes and the timed re-check) and is lifted when it is not.
 */
class OverlayManager(private val context: Context) {
    companion object {
        /** A patch outlives the pass that drew it by this much; the timed re-check refreshes it sooner. */
        const val HOLD_MS = 30_000L
    }

    private val wm = context.getSystemService(WindowManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private var view: PatchView? = null
    private val patches = ArrayList<Patch>()

    /** [solid] patches blank an app switcher thumbnail; region passes never lift them. */
    private class Patch(val region: Rect, val bitmap: Bitmap, var until: Long, val solid: Boolean = false)

    private inner class PatchView(context: Context) : View(context) {
        override fun onDraw(canvas: Canvas) {
            val now = System.currentTimeMillis()
            val loc = IntArray(2).also { getLocationOnScreen(it) }
            synchronized(patches) {
                for (p in patches) {
                    if (p.until < now) continue
                    canvas.drawBitmap(p.bitmap, (p.region.left - loc[0]).toFloat(), (p.region.top - loc[1]).toFloat(), null)
                }
            }
        }
    }

    /** [found] rectangles are screen coordinates; [frame]'s top-left sits at ([dx], [dy]) on the screen. */
    /**
     * With [replace] (a region pass that saw every picture on screen without the overlay in its way),
     * patches not found again are lifted at once: the picture scrolled away or changed. Otherwise
     * patches are only added or refreshed, and expire after [holdMillis] if nothing refreshes them.
     */
    fun cover(found: List<Pair<Rect, Finding>>, frame: Bitmap, dx: Int = 0, dy: Int = 0, holdMillis: Long = HOLD_MS,
              replace: Boolean = false, keep: Collection<Rect> = emptyList()) {
        if (!Settings.canDrawOverlays(context)) return
        val now = System.currentTimeMillis()
        val fresh = found.map { (r, _) ->
            val crop = Pixels.crop(frame, Rect(r).apply { offset(-dx, -dy) })
            Patch(Rect(r), Pixels.pixelate(crop, r.width(), r.height()), now + holdMillis)
        }
        synchronized(patches) {
            patches.removeAll { it.until < now }
            if (replace) patches.removeAll { old -> !old.solid && old.region !in keep && fresh.none { it.region == old.region } }
            for (p in fresh) {
                patches.removeAll { it.region == p.region }
                patches.add(p)
            }
        }
        main.post { if (patches.isEmpty()) hide() else show() }
    }

    /**
     * For screenshots that contain the overlay itself: a patch whose rectangle the accessibility tree
     * still reports is kept for another [holdMillis]; returns the rectangles that were held.
     */
    fun refreshIfCovered(rects: List<Rect>, holdMillis: Long = HOLD_MS): Set<Rect> {
        val now = System.currentTimeMillis()
        val held = HashSet<Rect>()
        synchronized(patches) {
            for (p in patches) if (p.until >= now && rects.any { it == p.region }) { p.until = now + holdMillis; held.add(p.region) }
        }
        return held
    }

    /**
     * Move every patch inside [area] (all of them when null) by the page's scroll: content scrolled
     * down by dy moves up by dy. A patch that leaves the scrolled area is dropped. Returns how many moved.
     */
    fun shift(dx: Int, dy: Int, area: Rect?): Int {
        if (dx == 0 && dy == 0) return 0
        var moved = 0
        synchronized(patches) {
            val it = patches.listIterator()
            while (it.hasNext()) {
                val p = it.next()
                if (area != null && !Rect.intersects(area, p.region)) continue
                val r = Rect(p.region).apply { offset(-dx, -dy) }
                if (area != null && !Rect.intersects(area, r)) { it.remove(); continue }
                it.set(Patch(r, p.bitmap, p.until, p.solid))
                moved++
            }
        }
        if (moved > 0) main.post { view?.invalidate() }
        return moved
    }

    /**
     * Blank these rectangles (app switcher thumbnails) in Watchman's ink for a few seconds; the next
     * switcher event refreshes them, and leaving the switcher lets them lapse. Old solid patches that
     * are not in [rects] go at once.
     */
    fun coverSolid(rects: List<Rect>, holdMillis: Long = 3000) {
        if (!Settings.canDrawOverlays(context)) return
        val now = System.currentTimeMillis()
        synchronized(patches) {
            patches.removeAll { it.solid && rects.none { r -> r == it.region } }
            for (r in rects) {
                val existing = patches.firstOrNull { it.solid && it.region == r }
                if (existing != null) { existing.until = now + holdMillis; continue }
                val bmp = Bitmap.createBitmap(maxOf(1, r.width()), maxOf(1, r.height()), Bitmap.Config.ARGB_8888)
                bmp.eraseColor(android.graphics.Color.parseColor("#0B1114"))
                patches.add(Patch(Rect(r), bmp, now + holdMillis, solid = true))
            }
        }
        main.post { if (patches.isEmpty()) hide() else show() }
    }

    /** Lift the patch over one region: the server said the picture is safe. */
    fun uncover(region: Rect) {
        synchronized(patches) { patches.removeAll { it.region == region } }
        main.post { if (patches.isEmpty()) hide() else view?.invalidate() }
    }

    fun clear() {
        synchronized(patches) { patches.clear() }
        main.post { hide() }
    }

    private fun show() {
        val v = view ?: PatchView(context).also {
            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT,
            )
            params.gravity = Gravity.TOP or Gravity.START
            wm.addView(it, params)
            view = it
        }
        v.invalidate()
        scheduleExpiry()
    }

    /** Wake when the soonest patch is due, drop what expired, and hide when nothing is left. */
    private fun scheduleExpiry() {
        main.removeCallbacks(expire)
        val next = synchronized(patches) { patches.minOfOrNull { it.until } } ?: return
        main.postDelayed(expire, maxOf(250L, next - System.currentTimeMillis() + 50))
    }

    private val expire = Runnable {
        val now = System.currentTimeMillis()
        val empty = synchronized(patches) { patches.removeAll { it.until < now }; patches.isEmpty() }
        if (empty) hide() else { view?.invalidate(); scheduleExpiry() }
    }

    private fun hide() {
        view?.let { runCatching { wm.removeView(it) } }
        view = null
    }
}
