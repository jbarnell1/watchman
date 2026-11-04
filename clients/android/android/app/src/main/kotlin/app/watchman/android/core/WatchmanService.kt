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
        const val ACTION_MISSED = "app.watchman.android.MISSED"
        /** UsbManager.ACTION_USB_STATE, hidden from the SDK but sent to every app. */
        private const val USB_STATE = "android.hardware.usb.action.USB_STATE"
        const val REJECTED_MESSAGE = "The server no longer accepts this device. Protection stays on. Pair again with a new code."
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

    private val screenReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                Intent.ACTION_SCREEN_ON -> JudgeTally.shared.screenOn()
                Intent.ACTION_SCREEN_OFF -> JudgeTally.shared.screenOff()
            }
        }
    }

    /** Android's USB state broadcast: whether a cable is in and adb runs on it. Sticky, so it is read at start too. */
    private val usbReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) { noteUsb(intent) }
    }

    private fun noteUsb(intent: Intent?) {
        if (intent == null || !intent.hasExtra("connected")) return
        val usb = DebugFlags.Usb(intent.getBooleanExtra("connected", false), intent.getBooleanExtra("adb", false))
        val before = state.usbDebugging
        val now = DebugFlags.remember(usb, before)
        if (now != before) {
            state.usbDebugging = now
            if (now == true) scope.launch { heartbeat(force = true) }
        }
    }

    override fun onCreate() {
        super.onCreate()
        state = AppState.get(this)
        createChannel()
        startForeground(NOTIFICATION_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        running = true
        reportGap()
        registerReceiver(screenReceiver, android.content.IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
        })
        try {
            val filter = android.content.IntentFilter(USB_STATE)
            noteUsb(if (Build.VERSION.SDK_INT >= 33) registerReceiver(usbReceiver, filter, Context.RECEIVER_EXPORTED)
                    else registerReceiver(usbReceiver, filter))
        } catch (e: Exception) { Log.w(TAG, "usb state", e) }
        if (getSystemService(android.os.PowerManager::class.java)?.isInteractive == true) JudgeTally.shared.screenOn()
        scope.launch { aliveLoop() }
        scope.launch { loop() }
        state.onChanged = { scope.launch { heartbeat(force = true) } }
    }

    /**
     * A stretch with this service not running is reported on the next start (fail closed and loud):
     * otherwise a phone switched off for less than the server's silence threshold goes unheard.
     */
    private fun reportGap() {
        try {
            if (state.pairing == null) return
            val now = System.currentTimeMillis()
            val bootedAt = now - android.os.SystemClock.elapsedRealtime()
            val pausedUntil = state.policy?.pausedUntil?.let { Iso.parse(it) }?.takeIf { it > 0 }
            ProtectionGap.decide(state.aliveAt, state.cleanStop, now, bootedAt, pausedUntil)?.let {
                Log.i(TAG, "protection gap: ${it.reason}, ${(it.toMillis - it.fromMillis) / 1000} s")
                state.record(ProtectionGap.event(it), null)
            }
            state.cleanStop = false
            state.aliveAt = now
        } catch (e: Exception) {
            Log.w(TAG, "gap", e)
        }
    }

    private suspend fun aliveLoop() {
        while (true) {
            delay(ProtectionGap.ALIVE_EVERY_MS)
            try { if (state.pairing != null) state.aliveAt = System.currentTimeMillis() } catch (e: Exception) { Log.w(TAG, "alive", e) }
        }
    }

    /** Developer options and USB debugging, for the heartbeat's `device` field; a value is null when unknown. */
    private fun deviceFlags(): Map<String, Boolean?>? = try {
        val cr = contentResolver
        DebugFlags.decide(
            android.provider.Settings.Global.getInt(cr, android.provider.Settings.Global.ADB_ENABLED, 0),
            android.provider.Settings.Global.getInt(cr, android.provider.Settings.Global.DEVELOPMENT_SETTINGS_ENABLED, 0),
            state.usbDebugging,
        )
    } catch (e: Exception) { null }

    /** A 401 never unpairs: the token, the policy and protection stay, and heartbeats go on. */
    private fun noteApiError(e: ApiException) {
        if (e.status == 401) {
            state.rejected = true
            lastError = REJECTED_MESSAGE
        } else {
            lastError = "${e.code}: ${e.message}"
        }
    }

    private suspend fun loop() {
        while (true) {
            try {
                flush()
                checkPolicy()
                val current = currentState()
                val due = System.currentTimeMillis() - lastHeartbeatAt > 5 * 60 * 1000
                if (due || current != lastReported) heartbeat(force = true)
            } catch (e: ApiException) {
                Log.w(TAG, "loop", e)
                noteApiError(e)
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
                val judge = JudgeTally.shared.take()
                val secure = SecureApps.shared.take()
                val r = try {
                    api.heartbeat(mapOf(
                        "sent_at" to Iso.now(), "app_version" to "0.1.0", "booted_at" to booted, "safe_mode" to packageManager.isSafeMode,
                        "battery_pct" to battery, "queued_events" to state.queue.size, "state" to current,
                        "metrics" to (if (work.isEmpty) null else work.toMap()),
                        "device" to deviceFlags(),
                        "judge" to judge.toMap(),
                        "secure_apps" to secure,
                    ))
                } catch (e: Exception) {
                    // Not delivered: keep it all for the next heartbeat.
                    Metrics.shared.giveBack(work)
                    JudgeTally.shared.giveBack(judge)
                    SecureApps.shared.giveBack(secure)
                    throw e
                }
                lastHeartbeatAt = System.currentTimeMillis()
                lastReported = current
                lastError = null
                state.rejected = false
                val policy = state.policy
                if (policy == null || policy.version != r.policyVersion || policy.pausedUntil != r.pausedUntil) {
                    state.policy = api.policy()
                }
                ensureModels(api)
            } catch (e: ApiException) {
                noteApiError(e)
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
        } catch (e: ApiException) {
            if (e.status == 401) noteApiError(e)
            Log.w(TAG, "policy check", e)
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
            .addAction(Notification.Action.Builder(null, getString(app.watchman.android.R.string.missed_action),
                PendingIntent.getService(this, 1, Intent(this, WatchmanService::class.java).setAction(ACTION_MISSED),
                    PendingIntent.FLAG_IMMUTABLE)).build())
            .setOngoing(true)
            .build()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_MISSED) {
            val watcher = app.watchman.android.capture.ScreenWatcher.instance
            val main = android.os.Handler(mainLooper)
            if (watcher == null) toast(getString(app.watchman.android.R.string.missed_not_running))
            else watcher.reportMiss { n ->
                main.post { toast(getString(if (n >= 0) app.watchman.android.R.string.missed_thanks else app.watchman.android.R.string.missed_not_running)) }
            }
        }
        return START_STICKY
    }

    private fun toast(text: String) = android.widget.Toast.makeText(this, text, android.widget.Toast.LENGTH_SHORT).show()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        running = false
        // A stop inside a partner-code pause already reported itself; see ProtectionGap.decide.
        try { if (state.policy?.isPaused() == true) state.cleanStop = true } catch (_: Exception) { }
        runCatching { unregisterReceiver(screenReceiver) }
        runCatching { unregisterReceiver(usbReceiver) }
        JudgeTally.shared.screenOff()
        scope.cancel()
        super.onDestroy()
    }
}
