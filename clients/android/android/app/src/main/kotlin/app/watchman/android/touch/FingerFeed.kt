package app.watchman.android.touch

import android.content.ComponentName
import android.content.Context
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.hardware.display.DisplayManager
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Parcel
import android.os.ParcelFileDescriptor
import android.util.Log
import android.view.Display
import android.view.Surface
import android.view.WindowManager
import app.watchman.android.core.Metrics
import app.watchman.android.core.Touch
import rikka.shizuku.Shizuku
import java.io.DataInputStream

/**
 * The app's side of finger tracking (ADR-044). When Shizuku runs and has allowed Watchman, it
 * starts [TouchService] in a shell-rights process and hands each finger sample, in screen pixels,
 * to [listener] on the main thread. Without Shizuku nothing changes: blurs follow scroll events.
 * Only used while blurs follow a scroll (follow_scroll); it never affects what is judged or covered.
 */
object FingerFeed {
    private const val TAG = "FingerFeed"
    private const val PERMISSION_REQUEST = 4401

    /** unavailable (Shizuku not running), not_allowed, starting, on, or failed. */
    @Volatile var state: String = "unavailable"
        private set
    /** kind (see [Touch]), x, y in screen pixels, uptime ms. Main thread. */
    @Volatile var listener: ((Int, Int, Int, Long) -> Unit)? = null

    private val main = Handler(Looper.getMainLooper())
    private var app: Context? = null
    private var started = false
    private var bound = false

    private val args by lazy {
        Shizuku.UserServiceArgs(ComponentName(app!!.packageName, TouchService::class.java.name))
            .daemon(false).processNameSuffix("touch").tag("touch").version(1)
    }

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            if (binder == null || !binder.pingBinder()) { state = "failed"; return }
            Thread({ read(binder) }, "watchman-finger").apply { isDaemon = true; start() }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            bound = false
            state = if (Shizuku.pingBinder()) "failed" else "unavailable"
        }
    }

    /** Listen for Shizuku; connects whenever it is running and has allowed Watchman. */
    fun start(context: Context) {
        if (started) return
        started = true
        app = context.applicationContext
        Shizuku.addBinderReceivedListenerSticky { connect() }
        Shizuku.addBinderDeadListener { bound = false; state = "unavailable" }
        Shizuku.addRequestPermissionResultListener { code, result ->
            if (code != PERMISSION_REQUEST) return@addRequestPermissionResultListener
            if (result == PackageManager.PERMISSION_GRANTED) connect() else state = "not_allowed"
        }
    }

    /** Ask Shizuku for permission (it shows its own dialog). False when Shizuku is not running. */
    fun requestPermission(): Boolean {
        if (!Shizuku.pingBinder()) { state = "unavailable"; return false }
        if (Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED) connect()
        else Shizuku.requestPermission(PERMISSION_REQUEST)
        return true
    }

    private fun connect() {
        if (!Shizuku.pingBinder()) { state = "unavailable"; return }
        if (Shizuku.isPreV11() || Shizuku.checkSelfPermission() != PackageManager.PERMISSION_GRANTED) { state = "not_allowed"; return }
        if (bound) return
        bound = true
        state = "starting"
        try { Shizuku.bindUserService(args, connection) }
        catch (e: Exception) { bound = false; state = "failed"; Log.w(TAG, "bind failed", e) }
    }

    private fun read(binder: IBinder) {
        val data = Parcel.obtain()
        val reply = Parcel.obtain()
        val pipe: ParcelFileDescriptor
        val maxX: Int
        val maxY: Int
        try {
            binder.transact(TouchService.OPEN, data, reply, 0)
            if (reply.readInt() != 1) { state = "failed"; Log.w(TAG, "no touch: ${reply.readString()}"); return }
            maxX = reply.readInt()
            maxY = reply.readInt()
            pipe = ParcelFileDescriptor.CREATOR.createFromParcel(reply)
        } catch (e: Exception) {
            state = "failed"; Log.w(TAG, "open failed", e); return
        } finally { data.recycle(); reply.recycle() }
        state = "on"
        val display = app!!.getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)
        val bounds = app!!.getSystemService(WindowManager::class.java).maximumWindowMetrics.bounds
        val metrics = Metrics.shared
        // The touchscreen reports in the display's natural (portrait) orientation.
        var sx = 1.0
        var sy = 1.0
        var rotated = false
        try {
            DataInputStream(ParcelFileDescriptor.AutoCloseInputStream(pipe)).use { input ->
                while (true) {
                    val kind = input.readByte().toInt()
                    val x = input.readInt()
                    val y = input.readInt()
                    val t = input.readLong()
                    if (kind == Touch.DOWN) {
                        val rotation = display?.rotation ?: Surface.ROTATION_0
                        rotated = rotation != Surface.ROTATION_0
                        val w = if (rotation == Surface.ROTATION_90 || rotation == Surface.ROTATION_270) bounds.height() else bounds.width()
                        val h = if (rotation == Surface.ROTATION_90 || rotation == Surface.ROTATION_270) bounds.width() else bounds.height()
                        sx = w.toDouble() / (maxX + 1)
                        sy = h.toDouble() / (maxY + 1)
                        metrics.count("finger_touches")
                    }
                    // Landscape is left to scroll events in this first version.
                    if (rotated) continue
                    val px = (x * sx).toInt()
                    val py = (y * sy).toInt()
                    main.post { listener?.invoke(kind, px, py, t) }
                }
            }
        } catch (e: Exception) {
            Log.i(TAG, "touch feed ended: ${e.message}")
        }
        state = if (Shizuku.pingBinder()) "failed" else "unavailable"
        bound = false
    }
}
