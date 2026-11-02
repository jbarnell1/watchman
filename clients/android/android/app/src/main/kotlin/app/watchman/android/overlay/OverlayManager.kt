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
 * its crop so the picture's colour and layout stay while detail goes. Patches expire after a few
 * seconds unless the next pass finds the same region again.
 */
class OverlayManager(private val context: Context) {
    private val wm = context.getSystemService(WindowManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private var view: PatchView? = null
    private val patches = ArrayList<Patch>()

    private class Patch(val region: Rect, val bitmap: Bitmap, var until: Long)

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
    fun cover(found: List<Pair<Rect, Finding>>, frame: Bitmap, dx: Int = 0, dy: Int = 0, holdMillis: Long = 8000) {
        if (!Settings.canDrawOverlays(context)) return
        val now = System.currentTimeMillis()
        val fresh = found.map { (r, _) ->
            val crop = Pixels.crop(frame, Rect(r).apply { offset(-dx, -dy) })
            Patch(Rect(r), Pixels.pixelate(crop, r.width(), r.height()), now + holdMillis)
        }
        synchronized(patches) {
            patches.removeAll { it.until < now }
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
    fun refreshIfCovered(rects: List<Rect>, holdMillis: Long = 8000): Set<Rect> {
        val now = System.currentTimeMillis()
        val held = HashSet<Rect>()
        synchronized(patches) {
            for (p in patches) if (p.until >= now && rects.any { it == p.region }) { p.until = now + holdMillis; held.add(p.region) }
        }
        return held
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
        main.postDelayed({ v.invalidate(); if (patches.none { it.until > System.currentTimeMillis() }) hide() }, 8500)
    }

    private fun hide() {
        view?.let { runCatching { wm.removeView(it) } }
        view = null
    }
}
