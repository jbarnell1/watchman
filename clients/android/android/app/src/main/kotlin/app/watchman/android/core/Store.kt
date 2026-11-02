package app.watchman.android.core

import java.io.File

/** Key-value text storage behind an interface: EncryptedSharedPreferences on the phone, memory in tests. */
interface KeyValue {
    fun get(key: String): String?
    fun put(key: String, value: String?)
}

class MemoryKeyValue : KeyValue {
    private val map = HashMap<String, String>()
    override fun get(key: String): String? = map[key]
    override fun put(key: String, value: String?) { if (value == null) map.remove(key) else map[key] = value }
}

/** Events wait here until the server takes them. At most 200 per post, 5000 kept. */
class EventQueue(private val store: KeyValue) {
    companion object {
        const val MAX_BATCH = 200
        const val MAX_QUEUE = 5000
        private const val KEY = "event_queue"
    }

    @Synchronized fun all(): MutableList<WatchmanEvent> {
        val text = store.get(KEY) ?: return mutableListOf()
        val read = try { gson.fromJson(text, Array<WatchmanEvent>::class.java).toList() } catch (_: Exception) { emptyList() }
        // Gson fills fields it cannot find with null, whatever Kotlin says; an event an older build
        // wrote under other names has no id and could never be delivered, so it is dropped here.
        @Suppress("SENSELESS_COMPARISON", "USELESS_ELVIS")
        val ok = read.filter { (it.id ?: "").isNotBlank() && (it.type ?: "").isNotBlank() && (it.occurredAt ?: "").isNotBlank() }
        if (ok.size != read.size) store.put(KEY, gson.toJson(ok))
        return ok.toMutableList()
    }

    @Synchronized fun push(e: WatchmanEvent) {
        val q = all()
        q.add(e)
        while (q.size > MAX_QUEUE) q.removeAt(0)
        store.put(KEY, gson.toJson(q))
    }

    val size: Int get() = all().size

    /** Sends one batch; keeps what the server did not take. Returns ids the server wants images for. */
    fun flush(send: (List<WatchmanEvent>) -> EventsResponse): List<String> {
        val batch = synchronized(this) { all().take(MAX_BATCH) }
        if (batch.isEmpty()) return emptyList()
        val r = send(batch)
        // Rejected events are malformed; sending them again would fail again.
        val done = (r.accepted + r.duplicates + (r.rejected ?: emptyList()).mapNotNull { it.id }).toHashSet()
        synchronized(this) { store.put(KEY, gson.toJson(all().filter { it.id !in done })) }
        return r.upload
    }
}

/** JPEG crops kept on disk (app-private, no backup) until the server asks for them or ten minutes pass. */
class PendingImages(private val dir: File) {
    fun put(eventId: String, jpeg: ByteArray) {
        dir.mkdirs()
        File(dir, "$eventId.jpg").writeBytes(jpeg)
        prune()
    }

    fun take(eventId: String): ByteArray? {
        val f = File(dir, "$eventId.jpg")
        if (!f.exists()) return null
        val bytes = f.readBytes()
        f.delete()
        return bytes
    }

    fun prune(maxAgeMillis: Long = 10 * 60 * 1000) {
        val cutoff = System.currentTimeMillis() - maxAgeMillis
        dir.listFiles()?.filter { it.lastModified() < cutoff }?.forEach { it.delete() }
    }
}
