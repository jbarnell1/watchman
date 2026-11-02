package app.watchman.android.core

import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.google.gson.annotations.SerializedName

// Shapes from docs/api-contract.md and models/manifest.json. Gson with snake_case field names.

val gson: Gson = GsonBuilder().disableHtmlEscaping().create()

data class Thresholds(val cover: Double, val flag: Double, @SerializedName("detector_cover") val detectorCover: Double,
                      val ask: Double? = null, val sure: Double? = null)

data class Policy(
    val version: Int,
    val thresholds: Thresholds,
    @SerializedName("strict_mode") val strictMode: Boolean,
    val coverage: String? = null,
    @SerializedName("explicit_labels") val explicitLabels: List<String>? = null,
    @SerializedName("risky_apps") val riskyApps: List<String>,
    @SerializedName("blocked_apps") val blockedApps: List<String>,
    @SerializedName("protected_browser") val protectedBrowser: String,
    @SerializedName("sample_interval_s") val sampleIntervalS: Int,
    @SerializedName("whole_screen_interval_s") val wholeScreenIntervalS: Int,
    @SerializedName("paused_until") val pausedUntil: String?,
    @SerializedName("known_bad_hashes") val knownBadHashes: List<String>,
    @SerializedName("cover_on_device") val coverOnDevice: Boolean? = null,
    @SerializedName("ask_server") val askServer: Boolean? = null,
    @SerializedName("exempt_apps") val exemptApps: List<String>? = null,
    @SerializedName("watch_default") val watchDefault: String? = null,
    @SerializedName("realtime_apps") val realtimeApps: List<String>? = null,
    @SerializedName("checked_apps") val checkedApps: List<String>? = null,
    @SerializedName("checked_interval_s") val checkedIntervalS: Int? = null,
) {
    /** How the app in front is watched (ADR-037). An older server sends no levels: real time. */
    fun levelOf(pkg: String): WatchLevel = when {
        exemptApps?.contains(pkg) == true -> WatchLevel.OFF
        checkedApps?.contains(pkg) == true -> WatchLevel.CHECKED
        realtimeApps?.contains(pkg) == true -> WatchLevel.REALTIME
        watchDefault == "checked" -> WatchLevel.CHECKED
        else -> WatchLevel.REALTIME
    }

    fun isPaused(nowMillis: Long = System.currentTimeMillis()): Boolean =
        pausedUntil != null && Iso.parse(pausedUntil) > nowMillis
}

enum class WatchLevel { REALTIME, CHECKED, OFF }

data class Pairing(
    val server: String,
    val token: String,
    @SerializedName("device_id") val deviceId: String,
    @SerializedName("person_id") val personId: String,
    @SerializedName("server_name") val serverName: String,
)

data class ModelInput(val name: String, val size: Int, val layout: String, val color: String, val scale: Double,
                      val mean: DoubleArray, val std: DoubleArray, val resize: String)

data class ModelOutput(val name: String, val kind: String, val labels: List<String>, val positive: String?,
                       @SerializedName("explicit_labels") val explicitLabels: List<String>?,
                       val positives: List<String>? = null)

data class ModelEntry(val role: String, val file: String, val url: String, val sha256: String, val bytes: Long,
                      val input: ModelInput, val output: ModelOutput)

data class ModelsManifest(val version: Int, val models: List<ModelEntry>)

data class Region(val w: Int, val h: Int, @SerializedName("screen_w") val screenW: Int, @SerializedName("screen_h") val screenH: Int)

data class WatchmanEvent(
    val id: String = java.util.UUID.randomUUID().toString(),
    val type: String,
    @SerializedName("occurred_at") val occurredAt: String,
    val app: String? = null,
    val url: String? = null,
    val title: String? = null,
    val action: String = "none",
    val scores: Map<String, Any?>? = null,
    val region: Region? = null,
    val hash: String? = null,
    @SerializedName("has_image") val hasImage: Boolean = false,
    val detail: Map<String, Any?> = emptyMap(),
)

data class RejectedEvent(val id: String?, val reason: String?)

data class EventsResponse(val accepted: List<String>, val duplicates: List<String>, val upload: List<String>,
                          val rejected: List<RejectedEvent>? = null)

data class JudgeResponse(val score: Double, val verdict: String)

data class HeartbeatResponse(@SerializedName("policy_version") val policyVersion: Int,
                             @SerializedName("paused_until") val pausedUntil: String?)

/** What the heartbeat reports; every field that turns off is an alert on the server (ADR-015). */
data class DeviceState(
    val protection: Boolean,
    val accessibility: Boolean,
    val capture: String,        // granted | denied
    val overlay: Boolean,
    @SerializedName("device_admin") val deviceAdmin: Boolean,
    val extension: Boolean? = null,
    val vpn: Boolean? = null,
)

object Iso {
    private val format = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US).apply {
        timeZone = java.util.TimeZone.getTimeZone("UTC")
    }

    fun now(): String = synchronized(format) { format.format(java.util.Date()) }

    fun parse(s: String): Long = synchronized(format) {
        try { format.parse(s.replace(Regex("\\.\\d+"), ""))!!.time } catch (e: Exception) { 0L }
    }
}
