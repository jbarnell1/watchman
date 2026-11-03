package app.watchman.android.access

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import app.watchman.android.capture.ScreenWatcher
import app.watchman.android.core.AppState
import app.watchman.android.core.RegionTracker
import app.watchman.android.core.ScrollShift
import app.watchman.android.core.Iso
import app.watchman.android.core.WatchmanEvent
import app.watchman.android.overlay.BlockActivity

/**
 * Knows which app is in front and where its images are, and owns the screen watcher, which takes
 * its frames through this service's own screenshot API (ADR-026). Every scroll or content change
 * hands the image rectangles to the watcher, which judges them at their own size (ADR-002).
 * Blocked apps get the block screen and are sent home.
 */
class WatchAccessibilityService : AccessibilityService() {
    companion object {
        @Volatile var running = false
        @Volatile var instance: WatchAccessibilityService? = null
        @Volatile var foregroundPackage: String = ""
        @Volatile var lastUrl: String? = null
        /** Screens are watched whenever this service runs (API 30+ takes its own screenshots). */
        val screenshotsAvailable: Boolean get() = running
        private val browserPackages = setOf("org.mozilla.firefox", "com.android.chrome", "com.brave.browser", "com.microsoft.emmx",
            "com.opera.browser", "com.duckduckgo.mobile.android", "com.sec.android.app.sbrowser", "org.mozilla.fenix")
        private const val OWN_PACKAGE = "app.watchman.android"
    }

    private var lastBlockedAt = 0L
    private var lastScrollPkg = ""
    private var lastScrollAt = 0L
    private var watcher: ScreenWatcher? = null

    override fun onServiceConnected() {
        running = true
        instance = this
        watcher = ScreenWatcher(this)
        AppState.get(this).onChanged?.invoke()
        // After a (re)connect nothing reports the screen until the app changes something, and a
        // playing video changes nothing the tree shows: look at what is on screen now (ADR-043).
        android.os.Handler(mainLooper).postDelayed({
            val root = rootInActiveWindow ?: return@postDelayed
            val pkg = root.packageName?.toString() ?: return@postDelayed
            if (pkg == OWN_PACKAGE) return@postDelayed
            if (foregroundPackage.isEmpty()) foregroundPackage = pkg
            val regions = imageRegions(root)
            if (regions.isNotEmpty()) watcher?.judgeRegions(pkg, lastUrl, regions)
        }, 500)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val pkg = event.packageName?.toString() ?: return
        if (pkg == OWN_PACKAGE || pkg == "com.android.systemui") return
        // The keyboard opens its own window over the app; it is never the app in front (ADR-036).
        // Its own pictures (GIF and sticker search) are still judged below.
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && pkg != keyboardPackage()) {
            if (pkg != foregroundPackage) { foregroundPackage = pkg; watcher?.foregroundChanged() }
            if (isBlocked(pkg)) { block(pkg); return }
        }
        // Apps the household marked Not watched: nothing is judged while they are in front (ADR-030).
        if (isExempt(pkg)) return
        // The app switcher (drawn by the launcher) shows snapshots of other apps' windows, which never
        // include Watchman's overlay: cover the thumbnail of any app that had covered pictures (ADR-033).
        if (pkg == launcherPackage()) watcher?.coverSwitcher(switcherTiles(rootInActiveWindow))
        // A scroll moves the patches by the distance the app reports, before any screenshot: the blur
        // travels with the page instead of waiting for the next pass (ADR-032).
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_SCROLLED) {
            val dx = event.scrollDeltaX
            val dy = event.scrollDeltaY
            // How often apps really report a scroll, and with what distance (ADR-042).
            val metrics = app.watchman.android.core.Metrics.shared
            val now = android.os.SystemClock.uptimeMillis()
            if (pkg == lastScrollPkg && now - lastScrollAt < 1000) metrics.time("scroll_event_gap_ms", (now - lastScrollAt).toDouble())
            lastScrollPkg = pkg
            lastScrollAt = now
            metrics.count(if (ScrollShift.known(dx, dy)) "scroll_events" else "scroll_unknown_delta")
            // Only when the household turned scroll-following on; off by default (ADR-040).
            if (ScrollShift.known(dx, dy) && AppState.get(this).policy?.followScroll == true) {
                val area = android.graphics.Rect()
                event.source?.getBoundsInScreen(area)
                watcher?.shiftPatches(ScrollShift.of(dx), ScrollShift.of(dy), if (area.isEmpty) null else area)
            }
        }
        if (pkg in browserPackages) lastUrl = readUrl(rootInActiveWindow) ?: lastUrl
        val regions = imageRegions(rootInActiveWindow)
        if (regions.isNotEmpty()) watcher?.judgeRegions(pkg, lastUrl, regions)
    }

    private var launcher: String? = null

    /** The home app, which also draws the app switcher on Pixel and most phones. */
    fun launcherPackage(): String? = launcher ?: try {
        packageManager.resolveActivity(android.content.Intent(android.content.Intent.ACTION_MAIN)
            .addCategory(android.content.Intent.CATEGORY_HOME), 0)?.activityInfo?.packageName.also { launcher = it }
    } catch (_: Exception) { null }

    /** App thumbnails in the switcher, with the app's name as the switcher labels them. */
    fun switcherTiles(root: AccessibilityNodeInfo?): List<Pair<Rect, String>> {
        root ?: return emptyList()
        val out = ArrayList<Pair<Rect, String>>()
        walk(root, 0) { node ->
            val id = node.viewIdResourceName ?: ""
            val cls = node.className?.toString() ?: ""
            if (!(id.endsWith(":id/snapshot") || cls.contains("TaskThumbnail"))) return@walk
            val r = Rect().also { node.getBoundsInScreen(it) }
            if (r.width() < 100 || r.height() < 100) return@walk
            // The task's name sits on the thumbnail or an ancestor (Launcher3 TaskView, its icon).
            var label: String? = node.contentDescription?.toString()
            var p = node.parent
            var depth = 0
            while (label.isNullOrBlank() && p != null && depth < 4) {
                label = p.contentDescription?.toString()
                if (label.isNullOrBlank()) for (i in 0 until p.childCount) {
                    val c = p.getChild(i) ?: continue
                    if ((c.viewIdResourceName ?: "").endsWith(":id/icon") && !c.contentDescription.isNullOrBlank()) {
                        label = c.contentDescription.toString(); break
                    }
                }
                p = p.parent; depth++
            }
            out.add(r to (label ?: ""))
        }
        return out
    }

    private fun isExempt(pkg: String): Boolean = AppState.get(this).policy?.exemptApps?.contains(pkg) == true

    private fun keyboardPackage(): String? =
        android.provider.Settings.Secure.getString(contentResolver, android.provider.Settings.Secure.DEFAULT_INPUT_METHOD)
            ?.substringBefore('/')

    private fun isBlocked(pkg: String): Boolean {
        val policy = AppState.get(this).policy ?: return false
        if (policy.isPaused()) return false
        return pkg in policy.blockedApps
    }

    private fun block(pkg: String) {
        val now = System.currentTimeMillis()
        if (now - lastBlockedAt < 2000) return
        lastBlockedAt = now
        // The block screen covers the app and offers the home screen; going home first hid it.
        BlockActivity.show(this, pkg)
        AppState.get(this).record(WatchmanEvent(type = "app_blocked", occurredAt = Iso.now(), app = pkg, action = "blocked"), null)
    }

    /** Rectangles of image nodes on screen, largest first, at most 40. */
    fun imageRegions(root: AccessibilityNodeInfo?): List<RegionTracker.Region> {
        root ?: return emptyList()
        val out = ArrayList<RegionTracker.Region>()
        walk(root, 0) { node ->
            if (!node.isVisibleToUser) return@walk
            val r = Rect()
            node.getBoundsInScreen(r)
            val cls = node.className?.toString() ?: ""
            val video = app.watchman.android.core.RegionRule.isVideo(cls, r.width(), r.height())
            val picture = video || app.watchman.android.core.RegionRule.isPicture(
                cls, !node.contentDescription.isNullOrEmpty(), !node.text.isNullOrEmpty(),
                node.childCount, node.isEditable, r.width(), r.height())
            if (picture) {
                // One track per node and place (ADR-038).
                val key = "${node.viewIdResourceName ?: cls}@${r.left},${r.top},${r.width()}x${r.height()}"
                out.add(RegionTracker.Region(key, r.left, r.top, r.right, r.bottom, video))
            }
        }
        return out.sortedByDescending { it.area }.take(40)
    }

    private fun walk(node: AccessibilityNodeInfo, depth: Int, visit: (AccessibilityNodeInfo) -> Unit) {
        if (depth > 40) return
        visit(node)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            walk(child, depth + 1, visit)
        }
    }

    /** The address bar's text in the common browsers, when it is on screen. */
    fun readUrl(root: AccessibilityNodeInfo?): String? {
        root ?: return null
        for (id in listOf("org.mozilla.firefox:id/mozac_browser_toolbar_url_view", "org.mozilla.fenix:id/mozac_browser_toolbar_url_view",
            "com.android.chrome:id/url_bar", "com.brave.browser:id/url_bar", "com.microsoft.emmx:id/url_bar")) {
            val nodes = root.findAccessibilityNodeInfosByViewId(id)
            val text = nodes.firstOrNull()?.text?.toString()
            if (!text.isNullOrBlank()) return text
        }
        return null
    }

    override fun onInterrupt() { }

    override fun onDestroy() {
        running = false
        instance = null
        watcher?.stop()
        watcher = null
        AppState.get(this).onChanged?.invoke()
        super.onDestroy()
    }
}
