package app.watchman.android.core

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import app.watchman.android.MainActivity
import app.watchman.android.access.WatchAccessibilityService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File
import java.security.MessageDigest

/**
 * The always-on part: heartbeats every five minutes and at once when the protection state changes,
 * the event queue every 30 seconds, policy when its version changes, models from the server.
 */
class WatchmanService : Service() {
    companion object {
        const val CHANNEL = "watchman"
        const val NOTIFICATION_ID = 1
        private const val TAG = "WatchmanService"
        @Volatile var running = false
        @Volatile var lastHeartbeatAt: Long = 0
        @Volatile var lastError: String? = null

        fun start(context: Context) {
            val intent = Intent(context, WatchmanService::class.java)
            context.startForegroundService(intent)
        }

        fun modelsDir(context: Context): File = File(context.filesDir, "models")
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var state: AppState
    private var lastReported: DeviceState? = null
    private val booted = Iso.now()

    override fun onCreate() {
        super.onCreate()
        state = AppState.get(this)
        createChannel()
        startForeground(NOTIFICATION_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        running = true
        scope.launch { loop() }
        state.onChanged = { scope.launch { heartbeat(force = true) } }
    }

    private suspend fun loop() {
        while (true) {
            try {
                flush()
                checkPolicy()
                val current = currentState()
                val due = System.currentTimeMillis() - lastHeartbeatAt > 5 * 60 * 1000
                if (due || current != lastReported) heartbeat(force = true)
            } catch (e: Exception) {
                Log.w(TAG, "loop", e)
                lastError = e.message
            }
            delay(30_000)
        }
    }

    fun currentState(): DeviceState = DeviceState(
        protection = WatchAccessibilityService.running,
        accessibility = WatchAccessibilityService.running,
        // Screens come through the accessibility service's own screenshots (ADR-026).
        capture = if (WatchAccessibilityService.running) "granted" else "denied",
        overlay = android.provider.Settings.canDrawOverlays(this),
        deviceAdmin = WatchmanDeviceAdmin.isActive(this),
    )

    private val heartbeatLock = Any()

    private fun heartbeat(force: Boolean) {
        val api = state.api() ?: return
        synchronized(heartbeatLock) {
            try {
                val current = currentState()
                if (!force && current == lastReported && System.currentTimeMillis() - lastHeartbeatAt < 5 * 60 * 1000) return
                val battery = (getSystemService(Context.BATTERY_SERVICE) as? android.os.BatteryManager)
                    ?.getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY)
                val work = Metrics.shared.takeHeartbeat()
                val r = try {
                    api.heartbeat(mapOf(
                        "sent_at" to Iso.now(), "app_version" to "0.1.0", "booted_at" to booted, "safe_mode" to packageManager.isSafeMode,
                        "battery_pct" to battery, "queued_events" to state.queue.size, "state" to current,
                        "metrics" to (if (work.isEmpty) null else work.toMap()),
                    ))
                } catch (e: Exception) {
                    Metrics.shared.giveBack(work) // not delivered: keep it for the next heartbeat
                    throw e
                }
                lastHeartbeatAt = System.currentTimeMillis()
                lastReported = current
                lastError = null
                val policy = state.policy
                if (policy == null || policy.version != r.policyVersion || policy.pausedUntil != r.pausedUntil) {
                    state.policy = api.policy()
                }
                ensureModels(api)
            } catch (e: ApiException) {
                if (e.status == 401) { state.pairing = null; state.policy = null }
                lastError = "${e.code}: ${e.message}"
                Log.w(TAG, "heartbeat", e)
            } catch (e: Exception) {
                lastError = e.message
                Log.w(TAG, "heartbeat", e)
            }
        }
    }

    /** Settings changed on the portal reach the phone within about 30 s (ADR-031). */
    private fun checkPolicy() {
        val api = state.api() ?: return
        try {
            val v = api.policyVersion()
            val policy = state.policy
            if (policy == null || policy.version != v.policyVersion || policy.pausedUntil != v.pausedUntil) state.policy = api.policy()
        } catch (e: Exception) {
            Log.w(TAG, "policy check", e)
        }
    }

    private fun flush() {
        val api = state.api() ?: return
        val wanted = state.queue.flush { api.events(it) }
        for (id in wanted) {
            val jpeg = state.pending.take(id) ?: continue
            try { api.uploadImage(id, jpeg) } catch (e: Exception) { Log.w(TAG, "upload $id", e) }
        }
    }

    private fun ensureModels(api: Api) {
        // Look at the manifest every few hours even when a classifier is in place: a newer server may
        // offer a model this phone does not have yet (the sorter, ADR-034).
        if (state.classifier != null && state.classifierFile(this).exists() &&
            System.currentTimeMillis() - manifestCheckedAt < MANIFEST_RECHECK_MS) return
        val manifest = api.manifest()
        val classifier = manifest.models.firstOrNull { it.role == "crop_classifier_int8" } ?: manifest.models.firstOrNull { it.role == "crop_classifier" } ?: return
        val detector = manifest.models.firstOrNull { it.role == "detector_small" }
        val gate = manifest.models.firstOrNull { it.role == "crop_gate" }
        for (entry in listOfNotNull(classifier, detector, gate)) {
            val target = File(modelsDir(this), entry.file)
            if (target.exists() && sha256(target) == entry.sha256) continue
            Log.i(TAG, "downloading ${entry.file}")
            val bytes = api.modelBytes(entry.url)
            val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            if (digest != entry.sha256) throw IllegalStateException("${entry.file} hash mismatch")
            target.parentFile?.mkdirs()
            target.writeBytes(bytes)
        }
        state.classifier = classifier
        state.detector = detector
        state.gate = gate
        manifestCheckedAt = System.currentTimeMillis()
    }

    private var manifestCheckedAt = 0L
    private val MANIFEST_RECHECK_MS = 6 * 60 * 60 * 1000L

    private fun sha256(f: File): String = MessageDigest.getInstance("SHA-256").digest(f.readBytes()).joinToString("") { "%02x".format(it) }

    private fun createChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Watchman", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Shown while protection is on."
        })
    }

    private fun notification(): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setContentTitle(getString(app.watchman.android.R.string.notification_title))
            .setContentText(getString(app.watchman.android.R.string.notification_text))
            .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        running = false
        scope.cancel()
        super.onDestroy()
    }
}
