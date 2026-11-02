package app.watchman.android.access

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import app.watchman.android.capture.ScreenWatcher
import app.watchman.android.core.AppState
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
    private var watcher: ScreenWatcher? = null

    override fun onServiceConnected() {
        running = true
        instance = this
        watcher = ScreenWatcher(this)
        AppState.get(this).onChanged?.invoke()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val pkg = event.packageName?.toString() ?: return
        if (pkg == OWN_PACKAGE || pkg == "com.android.systemui") return
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            foregroundPackage = pkg
            if (isBlocked(pkg)) { block(pkg); return }
        }
        // Apps the household marked Not watched: nothing is judged while they are in front (ADR-030).
        if (isExempt(pkg)) return
        // A scroll moves the patches by the distance the app reports, before any screenshot: the blur
        // travels with the page instead of waiting for the next pass (ADR-032).
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_SCROLLED) {
            val dx = event.scrollDeltaX
            val dy = event.scrollDeltaY
            if (ScrollShift.known(dx, dy)) {
                val area = android.graphics.Rect()
                event.source?.getBoundsInScreen(area)
                watcher?.shiftPatches(ScrollShift.of(dx), ScrollShift.of(dy), if (area.isEmpty) null else area)
            }
        }
        if (pkg in browserPackages) lastUrl = readUrl(rootInActiveWindow) ?: lastUrl
        val regions = imageRegions(rootInActiveWindow)
        if (regions.isNotEmpty()) watcher?.judgeRegions(pkg, lastUrl, regions)
    }

    private fun isExempt(pkg: String): Boolean = AppState.get(this).policy?.exemptApps?.contains(pkg) == true

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
    fun imageRegions(root: AccessibilityNodeInfo?): List<Rect> {
        root ?: return emptyList()
        val out = ArrayList<Rect>()
        walk(root, 0) { node ->
            if (!node.isVisibleToUser) return@walk
            val r = Rect()
            node.getBoundsInScreen(r)
            val picture = app.watchman.android.core.RegionRule.isPicture(
                node.className?.toString() ?: "", !node.contentDescription.isNullOrEmpty(), !node.text.isNullOrEmpty(),
                node.childCount, node.isEditable, r.width(), r.height())
            if (picture) out.add(r)
        }
        return out.sortedByDescending { it.width() * it.height() }.take(40)
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
