package app.watchman.android.core

enum class Decision { SAFE, FLAG, COVER }

/** Scores become decisions here, and the same picture is not scored twice. */
object Decide {
    const val MIN_SIDE = 20
    const val SMALL_CROP = 160 // under this the crop classifier alone judges; above, the detector too

    fun fromScore(score: Double, t: Thresholds): Decision = when {
        score >= t.cover -> Decision.COVER
        score >= t.flag -> Decision.FLAG
        else -> Decision.SAFE
    }

    fun isTiny(w: Int, h: Int): Boolean = w < MIN_SIDE || h < MIN_SIDE

    fun isKnownBad(hash: String, knownBad: List<String>): Boolean = knownBad.any { DHash.matches(it, hash) }

    /** Between ask and cover the device covers first and asks the server (ADR-022), when the policy allows. */
    fun shouldAsk(score: Double, t: Thresholds, askServer: Boolean): Boolean {
        if (!askServer) return false
        val ask = t.ask ?: return false
        return score >= ask && score < t.cover
    }

    /** Report only (cover_on_device off): nothing is covered, what would have been is flagged. */
    fun settle(d: Decision, coverOnDevice: Boolean): Decision =
        if (!coverOnDevice && d == Decision.COVER) Decision.FLAG else d

    /** Combine the crop classifier and the detector: the detector only ever raises the answer. */
    fun combine(classifier: Double, detector: Double?, t: Thresholds): Decision {
        val base = fromScore(classifier, t)
        if (detector != null && detector >= t.detectorCover) return Decision.COVER
        return base
    }
}

data class Judged(val decision: Decision, val hash: String, val score: Double)

/** A bounded map that forgets the oldest entries. */
class Lru<K, V>(private val limit: Int) {
    private val map = object : LinkedHashMap<K, V>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<K, V>?): Boolean = size > limit
    }

    @Synchronized operator fun get(key: K): V? = map[key]
    @Synchronized operator fun set(key: K, value: V) { map[key] = value }
    @Synchronized fun nearby(predicate: (K) -> Boolean): V? = map.entries.firstOrNull { predicate(it.key) }?.value
    val size: Int get() = map.size
}

class DecisionCache {
    private val byHash = Lru<String, Judged>(4000)

    fun lookup(hash: String): Judged? = byHash[hash] ?: byHash.nearby { DHash.matches(it, hash) }
    fun remember(j: Judged) { byHash[j.hash] = j }
}
