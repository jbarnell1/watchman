package app.watchman.android.overlay

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/**
 * The full block screen, over a blocked app or a mostly explicit screen. It stays up and says what
 * happened: an app that only vanished looked like a crash. Leaving goes to the home screen, never
 * back into the blocked app.
 */
class BlockActivity : Activity() {
    companion object {
        private var lastShown = 0L
        private const val PASS_MS = 10 * 60 * 1000L
        private val passedAt = java.util.concurrent.ConcurrentHashMap<String, Long>()

        /** The person chose Continue anyway in [pkg] within the last 10 minutes (ADR-047). */
        fun passed(pkg: String): Boolean = (passedAt[pkg] ?: 0L) > System.currentTimeMillis() - PASS_MS

        /** [pkg] empty means a mostly covered screen in [front]; otherwise [pkg] is a blocked app. */
        fun show(context: Context, pkg: String, front: String = "") {
            if (System.currentTimeMillis() - lastShown < 1500) return
            lastShown = System.currentTimeMillis()
            context.startActivity(Intent(context, BlockActivity::class.java)
                .putExtra("pkg", pkg)
                .putExtra("front", front)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NO_ANIMATION))
        }

        /** The name people see for an app, or its package name when it has none. */
        fun label(context: Context, pkg: String): String = try {
            val pm = context.packageManager
            pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
        } catch (_: Exception) {
            pkg
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pkg = intent.getStringExtra("pkg") ?: ""
        val policy = app.watchman.android.core.AppState.get(this).policy
        val browser = policy?.protectedBrowser
        val browserInstalled = browser != null && try { packageManager.getPackageInfo(browser, 0); true } catch (_: Exception) { false }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.parseColor("#0B1114"))
            setPadding(72, 72, 72, 72)
        }
        root.addView(TextView(this).apply {
            text = if (pkg.isNotEmpty()) "${label(this@BlockActivity, pkg)} is blocked on this phone" else "Covered by Watchman"
            textSize = 24f
            setTextColor(Color.parseColor("#F2F5F6"))
            gravity = Gravity.CENTER
        })
        root.addView(TextView(this).apply {
            text = when {
                pkg.isEmpty() -> "Most of this screen was explicit. It has been reported."
                browserInstalled -> "Your household blocks this app. Use ${label(this@BlockActivity, browser!!)} instead, where " +
                    "Watchman checks images before they show. Opening a blocked app is in the next digest."
                browser != null -> "Your household blocks this app. The protected browser is not installed on this phone yet; " +
                    "ask the admin which browser to use. Opening a blocked app is in the next digest."
                else -> "Your household blocks this app. Opening it is in the next digest."
            }
            textSize = 15f
            setTextColor(Color.parseColor("#B4C0C4"))
            gravity = Gravity.CENTER
            setPadding(0, 28, 0, 40)
        })
        root.addView(Button(this).apply {
            text = "Go to the home screen"
            isAllCaps = false
            textSize = 16f
            setTextColor(Color.parseColor("#0B1114"))
            background = GradientDrawable().apply { setColor(Color.parseColor("#F2F5F6")); cornerRadius = 28f }
            setPadding(48, 24, 48, 24)
            setOnClickListener { goHome() }
        })
        val front = intent.getStringExtra("front") ?: ""
        if (pkg.isEmpty() && front.isNotEmpty()) {
            root.addView(Button(this).apply {
                text = "Continue anyway"
                isAllCaps = false
                textSize = 15f
                setTextColor(Color.parseColor("#F2F5F6"))
                background = GradientDrawable().apply { setStroke(2, Color.parseColor("#5B6B70")); cornerRadius = 28f }
                setPadding(48, 24, 48, 24)
                setOnClickListener { continueAnyway(front) }
            }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = 24 })
            root.addView(TextView(this).apply {
                text = "The pictures stay blurred. Your partners are told you continued."
                textSize = 13f
                setTextColor(Color.parseColor("#8A979B"))
                gravity = Gravity.CENTER
                setPadding(0, 16, 0, 0)
            })
        }
        setContentView(root)
    }

    /** Back to the app with the blurs in place; partners are alerted (ADR-047). */
    private fun continueAnyway(front: String) {
        passedAt[front] = System.currentTimeMillis()
        app.watchman.android.core.AppState.get(this).record(app.watchman.android.core.WatchmanEvent(
            type = "block_passed", occurredAt = app.watchman.android.core.Iso.now(), app = front,
            url = app.watchman.android.access.WatchAccessibilityService.lastUrl,
        ), null)
        finish()
    }

    private fun goHome() {
        startActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        finish()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // Back would land in the blocked app underneath.
        goHome()
    }
}
