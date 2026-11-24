package app.watchman.android.core

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.ConnectivityManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import java.io.File
import java.security.MessageDigest

/**
 * Updates from the household server (ADR-061). The heartbeat response names the APK for each ABI
 * and its SHA-256; the phone downloads its own on an unmetered network, checks the hash and hands
 * the file to Android's package installer. The first update asks for one tap (a notification);
 * once Watchman has installed itself, Android lets it update without asking.
 */
object Updater {
    private const val TAG = "Updater"
    const val CHANNEL = "updates"
    private const val NOTIFICATION_ID = 7
    const val RETRY_MS = 6 * 60 * 60 * 1000L
    private const val MAX_BYTES = 200L shl 20
    private val NAME = Regex("^watchman-android-[0-9A-Za-z.\\-]+-(arm64-v8a|armeabi-v7a|x86_64)\\.apk$")
    private val VERSION = Regex("^(\\d+)\\.(\\d+)\\.(\\d+)(?:-([0-9A-Za-z.]+))?$")

    /** 0.2.0-rc.1 < 0.2.0 < 0.2.1, as the server orders them. Anything that is not a version is never newer. */
    fun isNewer(candidate: String, current: String): Boolean {
        val a = VERSION.matchEntire(candidate.trim()) ?: return false
        val b = VERSION.matchEntire(current.trim()) ?: return false
        for (i in 1..3) {
            val c = a.groupValues[i].toInt().compareTo(b.groupValues[i].toInt())
            if (c != 0) return c > 0
        }
        val ap = a.groups[4]?.value
        val bp = b.groups[4]?.value
        if (ap == bp) return false
        if (ap == null) return true
        if (bp == null) return false
        return ap > bp
    }

    /** The file for this phone: the first of its ABIs the offer has, when the offer is newer. */
    fun pick(offer: UpdateOffer?, current: String, abis: List<String>): UpdateFile? {
        if (offer == null || !isNewer(offer.version, current)) return null
        for (abi in abis) {
            val f = offer.files.firstOrNull { it.variant == abi && NAME.matches(it.name) && it.size in 1..MAX_BYTES }
            if (f != null) return f
        }
        return null
    }

    fun dir(context: Context): File = File(context.filesDir, "updates")

    /** Called after each heartbeat. Never throws. */
    fun consider(context: Context, api: Api, offer: UpdateOffer?) {
        try {
            val file = pick(offer, app.watchman.android.BuildConfig.VERSION_NAME, Build.SUPPORTED_ABIS.toList()) ?: return
            val prefs = context.getSharedPreferences("updates", Context.MODE_PRIVATE)
            val now = System.currentTimeMillis()
            if (prefs.getString("version", null) == offer!!.version && now - prefs.getLong("at", 0) < RETRY_MS) return
            if (!context.packageManager.canRequestPackageInstalls()) {
                prefs.edit().putString("version", offer.version).putLong("at", now).apply()
                notify(context, offer.version, "Allow Watchman to install its updates",
                    PendingIntent.getActivity(context, 0, Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:${context.packageName}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE))
                return
            }
            val cm = context.getSystemService(ConnectivityManager::class.java)
            if (cm?.isActiveNetworkMetered != false) return   // waits for Wi-Fi; the Devices page shows it is behind
            prefs.edit().putString("version", offer.version).putLong("at", now).apply()
            val bytes = api.modelBytes(file.url)
            val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            if (!digest.equals(file.sha256, ignoreCase = true)) {
                Log.w(TAG, "${file.name} does not match its SHA-256; not installed")
                return
            }
            val apk = File(dir(context).apply { mkdirs(); listFiles()?.forEach { it.delete() } }, file.name)
            apk.writeBytes(bytes)
            install(context, apk, offer.version)
        } catch (e: Exception) {
            Log.w(TAG, "update", e)
        }
    }

    private fun install(context: Context, apk: File, version: String) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            setSize(apk.length())
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("base.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val result = Intent(context, UpdateReceiver::class.java).putExtra("version", version)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            session.commit(PendingIntent.getBroadcast(context, id, result, flags).intentSender)
        }
        Log.i(TAG, "installing $version")
    }

    fun notify(context: Context, version: String, text: String, tap: PendingIntent) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Updates", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "A new version from your household server."
        })
        nm.notify(NOTIFICATION_ID, Notification.Builder(context, CHANNEL)
            .setContentTitle("Watchman $version is ready")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build())
    }

    fun clear(context: Context) {
        context.getSystemService(NotificationManager::class.java)?.cancel(NOTIFICATION_ID)
    }
}

/** The package installer's answer. When Android wants a tap, the confirmation waits in a notification. */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val version = intent.getStringExtra("version") ?: "a new version"
        when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                               else intent.getParcelableExtra(Intent.EXTRA_INTENT)) ?: return
                Updater.notify(context, version, "Tap to install",
                    PendingIntent.getActivity(context, 1, confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            }
            PackageInstaller.STATUS_SUCCESS -> {
                Updater.clear(context)
                Updater.dir(context).listFiles()?.forEach { it.delete() }
            }
            else -> Log.w("Updater", "install of $version failed: $status ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}")
        }
    }
}
