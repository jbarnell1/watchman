package app.watchman.android

import android.app.admin.DevicePolicyManager
import android.content.Intent
import app.watchman.android.core.PairLink
import com.google.android.gms.common.moduleinstall.InstallStatusListener
import com.google.android.gms.common.moduleinstall.ModuleInstall
import com.google.android.gms.common.moduleinstall.ModuleInstallRequest
import com.google.android.gms.common.moduleinstall.ModuleInstallStatusUpdate
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScanner
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import app.watchman.android.access.WatchAccessibilityService
import app.watchman.android.capture.ScreenWatcher
import app.watchman.android.core.Api
import app.watchman.android.core.ApiException
import app.watchman.android.core.AppState
import app.watchman.android.core.WatchmanDeviceAdmin
import app.watchman.android.core.WatchmanService
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** The Flutter UI's door into the Kotlin side, one MethodChannel. */
class MainActivity : FlutterActivity() {
    private val scope = CoroutineScope(Dispatchers.Main)
    private var channel: MethodChannel? = null
    /** A pairing link the app was opened with, until the pairing page asks for it. */
    private var pendingLink: PairLink? = null

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val link = PairLink.parse(intent.dataString) ?: return
        val ch = channel
        if (ch != null) ch.invokeMethod("pairLink", link.toMap()) else pendingLink = link
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        pendingLink = PairLink.parse(intent?.dataString) ?: pendingLink
        val ch = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "watchman")
        channel = ch
        ch.setMethodCallHandler { call, result ->
            val state = AppState.get(this)
            when (call.method) {
                "status" -> result.success(status(state))
                "pair" -> scope.launch {
                    try {
                        val pairing = withContext(Dispatchers.IO) {
                            Api.pair(call.argument("server")!!, call.argument("code")!!, call.argument<String>("name")?.trim()?.takeIf { it.isNotEmpty() } ?: android.os.Build.MODEL)
                        }
                        state.pairing = pairing
                        state.policy = withContext(Dispatchers.IO) { state.api()!!.policy() }
                        WatchmanService.start(this@MainActivity)
                        result.success(status(state))
                    } catch (e: ApiException) { result.error(e.code, e.message, null) }
                    catch (e: Exception) { result.error("error", e.message, null) }
                }
                "unlock" -> scope.launch {
                    try {
                        val until = withContext(Dispatchers.IO) { state.api()!!.unlock(call.argument("code")!!) }
                        state.policy = withContext(Dispatchers.IO) { state.api()!!.policy() }
                        result.success(until)
                    } catch (e: ApiException) { result.error(e.code, e.message, null) }
                    catch (e: Exception) { result.error("error", e.message, null) }
                }
                "requestUnlock" -> scope.launch {
                    try {
                        withContext(Dispatchers.IO) { state.api()!!.requestUnlock(call.argument("minutes")!!, call.argument<String>("reason") ?: "") }
                        result.success(true)
                    } catch (e: Exception) { result.error("error", e.message, null) }
                }
                "open" -> { open(call.argument("what")!!); result.success(true) }
                "pendingPairLink" -> { result.success(pendingLink?.toMap()); pendingLink = null }
                "scanPairCode" -> scanPairCode(result)
                "startService" -> { WatchmanService.start(this); result.success(true) }
                else -> result.notImplemented()
            }
        }
    }

    private fun status(state: AppState): Map<String, Any?> {
        val pm = getSystemService(PowerManager::class.java)
        return mapOf(
            "paired" to (state.pairing != null),
            "serverName" to state.pairing?.serverName,
            "server" to state.pairing?.server,
            "policyVersion" to state.policy?.version,
            "pausedUntil" to state.policy?.pausedUntil,
            "modelReady" to (state.classifier != null && state.classifierFile(this).exists()),
            "accessibility" to WatchAccessibilityService.running,
            "capture" to ScreenWatcher.running,
            "overlay" to Settings.canDrawOverlays(this),
            "deviceAdmin" to WatchmanDeviceAdmin.isActive(this),
            "batteryUnrestricted" to pm.isIgnoringBatteryOptimizations(packageName),
            "serviceRunning" to WatchmanService.running,
            "lastHeartbeatAt" to WatchmanService.lastHeartbeatAt,
            "lastError" to (WatchmanService.lastError ?: ScreenWatcher.lastError),
            "queued" to state.queue.size,
            "classifyMs" to ScreenWatcher.avgClassifyMs,
            "provider" to ScreenWatcher.provider,
            "budget" to ScreenWatcher.budget,
            "work" to ScreenWatcher.work,
        )
    }

    private fun open(what: String) {
        val intent = when (what) {
            "accessibility" -> Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            "overlay" -> Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))
            "battery" -> Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
            "appInfo" -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName"))
            "deviceAdmin" -> Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN)
                .putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, WatchmanDeviceAdmin.component(this))
                .putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION, getString(R.string.device_admin_description))
            else -> return
        }
        startActivity(intent)
    }

    /**
     * Google's scanner lives in a Play services module that downloads on first use. Ask for it, say
     * so on the channel, and open the scanner the moment it is installed instead of failing.
     */
    private fun scanPairCode(result: MethodChannel.Result) {
        val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
        val scanner = GmsBarcodeScanning.getClient(this, options)
        val modules = ModuleInstall.getClient(this)
        modules.areModulesAvailable(scanner)
            .addOnSuccessListener { available ->
                if (available.areModulesAvailable()) { startScan(scanner, result); return@addOnSuccessListener }
                channel?.invokeMethod("scannerStatus", "Getting the scanner ready. It downloads once from Google Play.")
                var done = false
                val listener = object : InstallStatusListener {
                    override fun onInstallStatusUpdated(update: ModuleInstallStatusUpdate) {
                        if (done) return
                        when (update.installState) {
                            ModuleInstallStatusUpdate.InstallState.STATE_COMPLETED -> {
                                done = true; modules.unregisterListener(this); startScan(scanner, result)
                            }
                            ModuleInstallStatusUpdate.InstallState.STATE_FAILED,
                            ModuleInstallStatusUpdate.InstallState.STATE_CANCELED -> {
                                done = true; modules.unregisterListener(this)
                                result.error("scanner_unavailable", "The scanner could not be downloaded. Type the code instead.", null)
                            }
                            else -> { }
                        }
                    }
                }
                modules.installModules(ModuleInstallRequest.newBuilder().addApi(scanner).setListener(listener).build())
                    .addOnSuccessListener { response ->
                        if (response.areModulesAlreadyInstalled() && !done) {
                            done = true; modules.unregisterListener(listener); startScan(scanner, result)
                        }
                    }
                    .addOnFailureListener { e ->
                        if (!done) { done = true; result.error("scanner_unavailable", e.message ?: "The scanner could not be downloaded.", null) }
                    }
            }
            .addOnFailureListener { e -> result.error("scanner_unavailable", e.message ?: "Google Play services is not available.", null) }
    }

    private fun startScan(scanner: GmsBarcodeScanner, result: MethodChannel.Result) {
        scanner.startScan()
            .addOnSuccessListener { code ->
                val link = PairLink.parse(code.rawValue)
                if (link != null) result.success(link.toMap())
                else result.error("not_watchman", "That QR code is not a Watchman pairing code.", null)
            }
            .addOnCanceledListener { result.success(null) }
            .addOnFailureListener { e -> result.error("scan_failed", e.message ?: "The scanner could not start.", null) }
    }
}
