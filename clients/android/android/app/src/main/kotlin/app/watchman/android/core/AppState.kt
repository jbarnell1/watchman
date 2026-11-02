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

    var pairing: Pairing?
        get() = prefs.get("pairing")?.let { gson.fromJson(it, Pairing::class.java) }
        set(v) { prefs.put("pairing", v?.let { gson.toJson(it) }); onChanged?.invoke() }

    var policy: Policy?
        get() = prefs.get("policy")?.let { gson.fromJson(it, Policy::class.java) }
        set(v) { prefs.put("policy", v?.let { gson.toJson(it) }); onChanged?.invoke() }

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
