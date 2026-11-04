package app.watchman.android.core

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.io.File

/** Pairing, policy, models and the queue, sealed with the Android Keystore. One per process. */
class AppState private constructor(context: Context) {
    companion object {
        @Volatile private var instance: AppState? = null
        fun get(context: Context): AppState = instance ?: synchronized(this) { instance ?: AppState(context.applicationContext).also { instance = it } }
    }

    private val prefs: KeyValue = run {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        val p = EncryptedSharedPreferences.create(context, "watchman", key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV, EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM)
        object : KeyValue {
            override fun get(key: String): String? = p.getString(key, null)
            override fun put(key: String, value: String?) { p.edit().apply { if (value == null) remove(key) else putString(key, value) }.apply() }
        }
    }

    val queue = EventQueue(prefs)
    val pending = PendingImages(File(context.filesDir, "pending"))
    var onChanged: (() -> Unit)? = null

    /** Pairing again with a new code replaces the token and clears [rejected]. */
    var pairing: Pairing?
        get() = prefs.get("pairing")?.let { gson.fromJson(it, Pairing::class.java) }
        set(v) { prefs.put("pairing", v?.let { gson.toJson(it) }); prefs.put("rejected", null); onChanged?.invoke() }

    var policy: Policy?
        get() = prefs.get("policy")?.let { gson.fromJson(it, Policy::class.java) }
        set(v) { prefs.put("policy", v?.let { gson.toJson(it) }); onChanged?.invoke() }

    /** The policy protection runs on: the last one fetched, or the built-in defaults, never none. */
    val effectivePolicy: Policy get() = policy ?: Policy.DEFAULTS

    /**
     * The server answered 401. Token, policy and protection stay exactly as they were and the
     * heartbeats go on: a fake or hostile server must not be able to switch protection off.
     */
    var rejected: Boolean
        get() = prefs.get("rejected") == "1"
        set(v) { if (v != rejected) prefs.put("rejected", if (v) "1" else null) }

    /** Epoch ms the watcher service last said it was alive; see [ProtectionGap]. */
    var aliveAt: Long?
        get() = prefs.get("alive_at")?.toLongOrNull()
        set(v) { prefs.put("alive_at", v?.toString()) }

    /** Set only by a stop that already reported itself (a partner-code pause). */
    var cleanStop: Boolean
        get() = prefs.get("clean_stop") == "1"
        set(v) { prefs.put("clean_stop", if (v) "1" else null) }

    var classifier: ModelEntry?
        get() = prefs.get("classifier")?.let { gson.fromJson(it, ModelEntry::class.java) }
        set(v) { prefs.put("classifier", v?.let { gson.toJson(it) }) }

    var detector: ModelEntry?
        get() = prefs.get("detector")?.let { gson.fromJson(it, ModelEntry::class.java) }
        set(v) { prefs.put("detector", v?.let { gson.toJson(it) }) }

    /** The sorter model (ADR-034); null when the household server does not offer one. */
    var gate: ModelEntry?
        get() = prefs.get("gate")?.let { gson.fromJson(it, ModelEntry::class.java) }
        set(v) { prefs.put("gate", v?.let { gson.toJson(it) }) }

    fun gateFile(context: Context): File? = gate?.let { File(WatchmanService.modelsDir(context), it.file) }
    fun classifierFile(context: Context): File = File(WatchmanService.modelsDir(context), classifier?.file ?: "none")
    fun detectorFile(context: Context): File? = detector?.let { File(WatchmanService.modelsDir(context), it.file) }

    fun api(): Api? = pairing?.let { Api(it.server, it.token) }

    fun record(event: WatchmanEvent, jpeg: ByteArray?) {
        if (jpeg != null) pending.put(event.id, jpeg)
        queue.push(event)
    }
}
