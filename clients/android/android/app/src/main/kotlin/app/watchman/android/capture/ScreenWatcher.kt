package app.watchman.android.capture

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.BatteryManager
import android.os.Handler
import android.os.Build
import android.os.HandlerThread
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityWindowInfo
import app.watchman.android.core.AppState
import app.watchman.android.core.Decision
import app.watchman.android.core.Finding
import app.watchman.android.core.Iso
import app.watchman.android.core.Metrics
import app.watchman.android.core.MetricsLog
import app.watchman.android.core.OnnxScorer
import app.watchman.android.core.Pipeline
import app.watchman.android.core.Policy
import app.watchman.android.core.Power
import app.watchman.android.core.PowerBudget
import app.watchman.android.core.Region
import app.watchman.android.core.WatchmanEvent
import app.watchman.android.overlay.BlockActivity
import app.watchman.android.overlay.OverlayManager
import java.util.concurrent.Executor
import java.util.concurrent.Executors

/**
 * Looks at the screen through the accessibility service's own screenshot API (ADR-026): no
 * MediaProjection. Region passes on Android 14 and later screenshot only the app window in front,
 * which does not contain Watchman's overlay, so a covered picture is still seen under its patch
 * and the patch follows it or drops when it leaves (ADR-026 addendum); the whole-screen tick takes
 * the full display so the notification shade and picture-in-picture are judged too. Before that:
 * MediaProjection, so no recording indicator, no collapsed notifications and no consent dialog
 * after every restart. Frames are pulled, never streamed: one screenshot when there is a reason, a
 * region pass from the accessibility tree (coalesced) or the whole-screen tick. Android rate-limits
 * the API, so a frame younger than [MIN_FRAME_AGE_MS] is reused. A power budget stretches the tick
 * and drops the on-device detector as the battery falls; the ask band (ADR-022) sends the doubt to
 * the server instead. Lives as long as the accessibility service does.
 */
/** A frame and where its top-left sits on the screen; [window] is true when it is one app window. */
class Shot(val bitmap: Bitmap, val dx: Int, val dy: Int, val window: Boolean)

class ScreenWatcher(private val service: AccessibilityService) {
    companion object {
        private const val TAG = "ScreenWatcher"
        const val REGION_COALESCE_MS = 400L
        const val MIN_FRAME_AGE_MS = 1000L
        @Volatile var instance: ScreenWatcher? = null
        @Volatile var lastError: String? = null
        @Volatile var avgClassifyMs: Double = 0.0
        @Volatile var provider: String? = null
        /** The power budget in force, for the status page. */
        @Volatile var budget: String = "not started"
        /** Frames taken, screens judged and skipped, crops asked of the server. */
        @Volatile var work: String? = null
        val running: Boolean get() = instance != null
    }

    private val state: AppState = AppState.get(service)
    private val thread = HandlerThread("watchman-screen").apply { start() }
    private val handler = Handler(thread.looper)
    private val executor: Executor = Executor { handler.post(it) }
    private val asker = Executors.newSingleThreadExecutor()
    private val overlay = OverlayManager(service)
    private var scorer: OnnxScorer? = null
    private var pipeline: Pipeline? = null
    private var latest: Bitmap? = null
    private var latestAt = 0L
    private var latestWindow: Shot? = null
    private var latestWindowAt = 0L
    private var frameSeq = 0L
    private var judgedSeq = -1L
    private var screenOff = false
    private var screensExamined = 0
    private var screensSkipped = 0
    private var framesTaken = 0
    private var asked = 0
    private var pendingRegions: Triple<String, String?, List<Rect>>? = null
    /** The pictures the app last reported; the timed tick re-checks them so patches stay put. */
    private var lastRegions: Triple<String, String?, List<Rect>>? = null
    private var regionPassScheduled = false
    private val recentHashes = HashMap<String, Long>()
    private var stopped = false
    // ADR-025: every step is timed into the shared tallies; a minute goes to the log and Logcat.
    private val metrics = Metrics.shared
    private val log = MetricsLog(java.io.File(service.filesDir, "metrics.jsonl"))
    private val flush = object : Runnable {
        override fun run() {
            if (stopped) return
            val m = metrics.takeMinute()
            if (!m.isEmpty) log.append(mapOf("t" to Iso.now(), "kind" to "minute") + m.toMap())
            handler.postDelayed(this, 60_000)
        }
    }

    private val screenReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                Intent.ACTION_SCREEN_OFF -> handler.post { screenOff = true; overlay.clear() }
                Intent.ACTION_SCREEN_ON, Intent.ACTION_USER_PRESENT -> handler.post { screenOff = false; scheduleTick(0) }
            }
        }
    }

    init {
        instance = this
        service.registerReceiver(screenReceiver, IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_USER_PRESENT)
        })
        scheduleTick(0)
        handler.postDelayed(flush, 60_000)
    }

    private fun ensureModels(): Boolean {
        if (pipeline != null) return true
        val classifier = state.classifier ?: return false
        if (!state.classifierFile(service).exists()) return false
        return try {
            val s = OnnxScorer(classifier, state.classifierFile(service).absolutePath, state.detector, state.detectorFile(service)?.absolutePath)
            scorer = s
            pipeline = Pipeline(s, metrics = metrics)
            provider = s.provider
            lastError = null
            Log.i(TAG, "models loaded on ${s.provider}")
            true
        } catch (e: Exception) {
            lastError = e.message
            Log.w(TAG, "models", e)
            false
        }
    }

    private fun power(): Power {
        val i = service.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return Power(plugged = true, batteryPct = 100)
        val plugged = i.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0
        val level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, 100)
        val scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
        return Power(plugged, if (scale > 0) level * 100 / scale else 100)
    }

    /**
     * One screenshot, then [then] with the frame on the watcher thread. A frame younger than
     * [MIN_FRAME_AGE_MS] is reused (Android refuses screenshots taken too close together); a
     * secure window (banking, DRM) yields no frame and nothing runs.
     */
    private fun takeDisplay(then: (Bitmap?) -> Unit) {
        val now = System.currentTimeMillis()
        val have = latest
        if (have != null && now - latestAt < MIN_FRAME_AGE_MS) { metrics.count("frames_reused"); then(have); return }
        val asked = System.nanoTime()
        service.takeScreenshot(Display.DEFAULT_DISPLAY, executor, object : AccessibilityService.TakeScreenshotCallback {
            override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                metrics.time("screenshot_ms", (System.nanoTime() - asked) / 1_000_000.0)
                val copyStart = System.nanoTime()
                val hw = result.hardwareBuffer
                val wrapped = Bitmap.wrapHardwareBuffer(hw, result.colorSpace)
                val copy = wrapped?.copy(Bitmap.Config.ARGB_8888, false)
                wrapped?.recycle()
                hw.close()
                if (copy == null) { then(latest); return }
                metrics.time("copy_ms", (System.nanoTime() - copyStart) / 1_000_000.0)
                metrics.count("frames")
                latest?.recycle()
                latest = copy
                latestAt = System.currentTimeMillis()
                frameSeq++
                framesTaken++
                then(copy)
            }

            override fun onFailure(errorCode: Int) {
                when (errorCode) {
                    AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> { metrics.count("frames_too_soon"); then(latest) }
                    AccessibilityService.ERROR_TAKE_SCREENSHOT_SECURE_WINDOW -> { metrics.count("secure_windows"); then(null) }
                    else -> { metrics.count("screenshot_errors"); lastError = "screenshot error $errorCode"; then(null) }
                }
            }
        })
    }

    /** The active application window, or null (Android before 14, or nothing suitable in front). */
    private fun activeAppWindow(): AccessibilityWindowInfo? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) return null
        val apps = try { service.windows } catch (_: Exception) { emptyList() }
            .filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
        return apps.firstOrNull { it.isActive } ?: apps.firstOrNull { it.isFocused } ?: apps.maxByOrNull {
            val r = Rect(); it.getBoundsInScreen(r); r.width() * r.height()
        }
    }

    /**
     * A screenshot of the app window in front, without Watchman's overlay in it (API 34+). Falls back
     * to the display when there is no window API or no suitable window, or when the window refused.
     */
    private fun takeAppWindow(then: (Shot?) -> Unit) {
        val win = activeAppWindow()
        if (win == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            takeDisplay { b -> then(b?.let { Shot(it, 0, 0, window = false) }) }
            return
        }
        val bounds = Rect().also { win.getBoundsInScreen(it) }
        val have = latestWindow
        if (have != null && have.dx == bounds.left && have.dy == bounds.top && System.currentTimeMillis() - latestWindowAt < MIN_FRAME_AGE_MS) {
            metrics.count("window_frames_reused"); then(have); return
        }
        val asked = System.nanoTime()
        service.takeScreenshotOfWindow(win.id, executor, object : AccessibilityService.TakeScreenshotCallback {
            override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                metrics.time("window_screenshot_ms", (System.nanoTime() - asked) / 1_000_000.0)
                val hw = result.hardwareBuffer
                val wrapped = Bitmap.wrapHardwareBuffer(hw, result.colorSpace)
                val copy = wrapped?.copy(Bitmap.Config.ARGB_8888, false)
                wrapped?.recycle()
                hw.close()
                if (copy == null) { takeDisplay { b -> then(b?.let { Shot(it, 0, 0, window = false) }) }; return }
                metrics.count("window_frames")
                latestWindow?.bitmap?.recycle()
                val shot = Shot(copy, bounds.left, bounds.top, window = true)
                latestWindow = shot
                latestWindowAt = System.currentTimeMillis()
                framesTaken++
                then(shot)
            }

            override fun onFailure(errorCode: Int) {
                when (errorCode) {
                    AccessibilityService.ERROR_TAKE_SCREENSHOT_SECURE_WINDOW -> { metrics.count("secure_windows"); then(null) }
                    else -> {
                        metrics.count("window_screenshot_fallbacks")
                        takeDisplay { b -> then(b?.let { Shot(it, 0, 0, window = false) }) }
                    }
                }
            }
        })
    }

    private val tick = Runnable { wholeScreenTick() }

    private fun scheduleTick(delayMs: Long) {
        handler.removeCallbacks(tick)
        handler.postDelayed(tick, delayMs)
    }

    /** The whole-screen pass, at the policy's interval stretched by the power budget, only when the screen changed. */
    private fun wholeScreenTick() {
        if (stopped) return
        val policy = state.policy
        if (policy == null || !ensureModels()) { scheduleTick(30_000); return }
        val p = power()
        budget = PowerBudget.describe(p)
        val interval = policy.wholeScreenIntervalS * 1000L * PowerBudget.intervalMultiplier(p)
        when {
            policy.isPaused() -> { overlay.clear(); scheduleTick(interval) }
            screenOff -> scheduleTick(interval)
            policy.exemptApps?.contains(app.watchman.android.access.WatchAccessibilityService.foregroundPackage) == true -> {
                metrics.count("exempt_ticks")
                overlay.clear()
                scheduleTick(interval)
            }
            else -> takeDisplay { frame ->
                if (frame != null && frameSeq != judgedSeq) {
                    judgedSeq = frameSeq
                    screensExamined++
                    metrics.count("screens_judged")
                    judge(Shot(frame, 0, 0, window = false), listOf(Rect(0, 0, frame.width, frame.height)),
                        app.watchman.android.access.WatchAccessibilityService.foregroundPackage,
                        app.watchman.android.access.WatchAccessibilityService.lastUrl, p, policy)
                } else {
                    screensSkipped++
                    metrics.count("screens_unchanged")
                }
                // A still screen sends no accessibility events, so nothing else would keep its patches
                // fresh: re-check the pictures the app last reported (cache hits, a hash each).
                lastRegions?.let { (lp, lu, lr) ->
                    if (lp == app.watchman.android.access.WatchAccessibilityService.foregroundPackage) regionPass(lp, lu, lr, recheck = true)
                }
                updateWork()
                scheduleTick(interval)
            }
        }
    }

    /** Called by the accessibility service with image rectangles in screen pixels; one pass per [REGION_COALESCE_MS] with the latest set. */
    fun judgeRegions(pkg: String, url: String?, regions: List<Rect>) {
        handler.post {
            pendingRegions = Triple(pkg, url, regions)
            if (regionPassScheduled) return@post
            regionPassScheduled = true
            handler.postDelayed({
                regionPassScheduled = false
                val (p, u, r) = pendingRegions ?: return@postDelayed
                pendingRegions = null
                lastRegions = Triple(p, u, r)
                regionPass(p, u, r)
            }, REGION_COALESCE_MS)
        }
    }

    /** Judge the pictures the app reports. Cached pictures cost a hash, so re-checking is cheap. */
    private fun regionPass(p: String, u: String?, r: List<Rect>, recheck: Boolean = false) {
        val policy = state.policy ?: return
        if (stopped || screenOff || policy.isPaused() || !ensureModels()) return
        takeAppWindow { shot ->
            if (shot == null) return@takeAppWindow
            metrics.count(if (recheck) "region_rechecks" else "region_passes")
            // A display shot contains our own patches: a picture under a patch cannot be seen,
            // so its patch is kept while the picture's rectangle is still on screen.
            val todo = if (shot.window) r else {
                val held = overlay.refreshIfCovered(r)
                metrics.count("patches_held_blind", held.size.toLong())
                r.filter { it !in held }
            }
            judge(shot, todo, p, u, power(), policy, replace = shot.window)
            updateWork()
        }
    }

    private fun judge(shot: Shot, screenRegions: List<Rect>, pkg: String, url: String?, p: Power, policy: Policy,
                      replace: Boolean = false) {
        val pipe = pipeline ?: return
        val frame = shot.bitmap
        val whole = Rect(0, 0, frame.width, frame.height)
        // Screen rectangles into the shot's own coordinates; ones outside the window are not ours to judge.
        val regions = screenRegions.mapNotNull { s ->
            val r = Rect(s).apply { offset(-shot.dx, -shot.dy) }
            if (r.intersect(whole)) r else null
        }
        if (regions.isEmpty()) return
        if (!p.plugged) metrics.count("judged_on_battery")
        val findings = metrics.timed("judge_ms") {
            pipe.judge(frame, regions, policy.thresholds, policy.knownBadHashes, policy.explicitLabels,
                coverOnDevice = policy.coverOnDevice != false, askServer = policy.askServer != false,
                useDetector = PowerBudget.detectorAllowed(p))
        }
        metrics.count("findings", findings.size.toLong())
        // Findings back to screen coordinates for the overlay and the events.
        val onScreen = findings.map { it.copy(region = Rect(it.region).apply { offset(shot.dx, shot.dy) }) }
        apply(onScreen, pkg, url, shot, replace)
    }

    private fun apply(findings: List<Finding>, pkg: String, url: String?, shot: Shot, replace: Boolean = false) {
        val frame = shot.bitmap
        val screen = service.resources.displayMetrics
        pipeline?.let { avgClassifyMs = it.avgClassifyMs }
        val coverOnDevice = state.policy?.coverOnDevice != false
        val covered = findings.filter { it.decision == Decision.COVER }
        val area = covered.sumOf { it.region.width().toLong() * it.region.height() }.toDouble() / (frame.width.toDouble() * frame.height)
        val blocked = coverOnDevice && area > 0.4
        if (blocked) BlockActivity.show(service, pkg)
        else if (coverOnDevice) overlay.cover(covered.map { it.region to it }, frame, shot.dx, shot.dy, replace = replace)
        for (f in findings) {
            if (f.ask) ask(f, pkg, url, screen.widthPixels, screen.heightPixels)
            else record(f, pkg, url, screen.widthPixels, screen.heightPixels, blocked)
        }
        val now = System.currentTimeMillis()
        if (recentHashes.size > 2000) recentHashes.entries.removeIf { now - it.value > 10 * 60 * 1000 }
    }

    /** ADR-022: covered first; the server's bigger model decides; a safe answer lifts the patch and nothing is reported. */
    private fun ask(f: Finding, pkg: String, url: String?, screenW: Int, screenH: Int) {
        val api = state.api() ?: return
        val jpeg = f.jpeg ?: return
        asked++
        asker.execute {
            val t0 = System.nanoTime()
            val verdict = try { api.judge(jpeg).verdict } catch (e: Exception) { Log.w(TAG, "ask", e); null }
            metrics.time("ask_ms", (System.nanoTime() - t0) / 1_000_000.0)
            metrics.count(when (verdict) { "safe" -> "ask_safe"; "explicit" -> "ask_explicit"; else -> "ask_failed" })
            handler.post {
                when (verdict) {
                    "safe" -> { pipeline?.resolve(f.hash, Decision.SAFE, f.score); overlay.uncover(f.region) }
                    "explicit" -> { pipeline?.resolve(f.hash, Decision.COVER, f.score); record(f, pkg, url, screenW, screenH, blocked = false) }
                    else -> { } // unreachable server: stays covered this pass and is asked again when seen next
                }
            }
        }
    }

    private fun record(f: Finding, pkg: String, url: String?, screenW: Int, screenH: Int, blocked: Boolean) {
        val now = System.currentTimeMillis()
        val seen = recentHashes[f.hash]
        if (seen != null && now - seen < 10 * 60 * 1000) return
        recentHashes[f.hash] = now
        val type = if (blocked) "screen_blocked" else if (f.decision == Decision.COVER) "image_covered" else "image_flagged"
        state.record(WatchmanEvent(
            type = type, occurredAt = Iso.now(), app = pkg, url = url,
            action = if (blocked) "blocked" else if (f.decision == Decision.COVER) "covered" else "flagged",
            scores = mapOf("classifier" to Math.round(f.score * 1000) / 1000.0, "labels" to f.labels),
            region = Region(f.region.width(), f.region.height(), screenW, screenH),
            hash = f.hash, hasImage = f.jpeg != null,
        ), f.jpeg)
    }

    private fun updateWork() {
        work = "$framesTaken frames, $screensExamined screens judged, $screensSkipped unchanged, $asked asked"
    }

    fun stop() {
        stopped = true
        handler.removeCallbacks(flush)
        instance = null
        handler.removeCallbacks(tick)
        runCatching { service.unregisterReceiver(screenReceiver) }
        overlay.clear()
        asker.shutdown()
        handler.post {
            scorer?.close()
            latest?.recycle()
            latestWindow?.bitmap?.recycle()
            thread.quitSafely()
        }
    }
}
