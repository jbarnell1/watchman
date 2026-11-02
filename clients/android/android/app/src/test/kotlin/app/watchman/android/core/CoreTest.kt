package app.watchman.android.core

import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class CoreTest {
    private val vectors: File by lazy {
        var dir: File? = File("").absoluteFile
        while (dir != null && !File(dir, "models/test-vectors").exists()) dir = dir.parentFile
        File(dir!!, "models/test-vectors/dhash")
    }

    @Test
    fun dhashReproducesEveryCommittedVector() {
        // raw.json holds the same pixels as the PNGs; android.jar has no PNG decoder for unit tests.
        val doc = JsonParser.parseString(File(vectors, "raw.json").readText()).asJsonObject
        val list = doc.getAsJsonArray("vectors")
        assertTrue(list.size() >= 8)
        for (v in list) {
            val o = v.asJsonObject
            val w = o["width"].asInt
            val h = o["height"].asInt
            val pixels = o.getAsJsonArray("pixels")
            val argb = IntArray(w * h) { (0xFF shl 24) or pixels[it].asInt }
            assertEquals(o["file"].asString, o["dhash"].asString, DHash.compute(argb, w, h))
        }
    }

    @Test
    fun hammingAndTolerance() {
        assertEquals(4, DHash.hamming("0000000000000000", "000000000000000f"))
        assertTrue(DHash.matches("0000000000000000", "000000000000000f"))
        assertFalse(DHash.matches("0000000000000000", "00000000000000ff"))
    }

    @Test
    fun decisionsFollowThresholds() {
        val t = Thresholds(0.8, 0.6, 0.55)
        assertEquals(Decision.SAFE, Decide.fromScore(0.1, t))
        assertEquals(Decision.FLAG, Decide.fromScore(0.6, t))
        assertEquals(Decision.COVER, Decide.fromScore(0.8, t))
        assertEquals(Decision.COVER, Decide.combine(0.2, 0.6, t))
        assertEquals(Decision.FLAG, Decide.combine(0.65, 0.1, t))
        assertTrue(Decide.isTiny(16, 100))
        assertTrue(Decide.isKnownBad("9f3a0c1e5b7d2a47", listOf("9f3a0c1e5b7d2a46")))
    }

    @Test
    fun postureKnobsShapeDecisions() {
        val t = Thresholds(0.85, 0.85, 0.55, ask = 0.6)
        assertTrue(Decide.shouldAsk(0.7, t, askServer = true))
        assertFalse(Decide.shouldAsk(0.7, t, askServer = false))
        assertFalse(Decide.shouldAsk(0.9, t, askServer = true))
        assertFalse(Decide.shouldAsk(0.7, Thresholds(0.85, 0.85, 0.55), askServer = true)) // no ask line
        assertEquals(Decision.FLAG, Decide.settle(Decision.COVER, coverOnDevice = false))
        assertEquals(Decision.COVER, Decide.settle(Decision.COVER, coverOnDevice = true))
        assertEquals(Decision.SAFE, Decide.settle(Decision.SAFE, coverOnDevice = false))
    }

    @Test
    fun powerBudgetStretchesTheIntervalAsTheBatteryFalls() {
        assertEquals(1, PowerBudget.intervalMultiplier(Power(plugged = true, batteryPct = 5)))
        assertEquals(2, PowerBudget.intervalMultiplier(Power(plugged = false, batteryPct = 60)))
        assertEquals(4, PowerBudget.intervalMultiplier(Power(plugged = false, batteryPct = 15)))
        assertTrue(PowerBudget.detectorAllowed(Power(plugged = false, batteryPct = 60)))
        assertFalse(PowerBudget.detectorAllowed(Power(plugged = false, batteryPct = 15)))
        assertEquals("plugged in", PowerBudget.describe(Power(plugged = true, batteryPct = 100)))
    }

    @Test
    fun metricsBucketMergeAndPercentiles() {
        var clock = 0L
        val m = Metrics { clock }
        for (ms in listOf(3.0, 12.0, 14.0, 15.0, 30.0, 300.0)) m.time("classify_ms", ms)
        m.count("frames", 6)
        clock = 60_000
        val minute = m.takeMinute()
        assertEquals(60L, minute.windowS)
        val t = minute.timings["classify_ms"]!!
        assertEquals(6L, t.n)
        assertEquals(16.0, t.percentile(0.5), 1e-9)
        assertEquals(listOf(0L, 0L, 1L, 0L, 3L, 1L, 0L, 0L, 0L, 1L, 0L, 0L), t.buckets.toList())
        // The heartbeat window is separate and still holds everything.
        val beat = m.takeHeartbeat()
        assertEquals(6L, beat.counters["frames"])
        assertTrue(m.takeHeartbeat().isEmpty)
        m.giveBack(beat)
        assertEquals(6L, m.takeHeartbeat().counters["frames"])
        val map = beat.toMap()
        assertTrue(gson.toJson(map).contains("\"window_s\":60"))
    }

    @Test
    fun pairLinksFromTheQrCodeAndTheAppLink() {
        assertEquals(PairLink("https://watchman.example.ts.net", "K7Q-4MZ-P2D"),
            PairLink.parse("https://watchman.example.ts.net/pair?code=K7Q4MZP2D"))
        assertEquals(PairLink("https://watchman.example.ts.net", "K7Q-4MZ-P2D"),
            PairLink.parse("watchman://pair?server=https%3A%2F%2Fwatchman.example.ts.net%2F&code=k7q4mzp2d"))
        assertEquals(null, PairLink.parse("https://watchman.example.ts.net/other?code=K7Q4MZP2D"))
        assertEquals(null, PairLink.parse("watchman://pair?server=javascript%3Aalert(1)&code=K7Q4MZP2D"))
        assertEquals(null, PairLink.parse("https://x.ts.net/pair?code=ABC"))
        assertEquals(null, PairLink.parse("not a link"))
        assertEquals("K7Q-4MZ-P", PairLink.formatCode("k7q4mzp"))
        assertEquals("K7Q-4MZ-P2D", PairLink.formatCode("K7Q-4MZ-P2D-EXTRA"))
    }

    @Test
    fun unreadableAndRejectedEventsLeaveTheQueue() {
        val kv = MemoryKeyValue()
        // What a build with renamed fields left behind: no id, type or occurred_at under these names.
        kv.put("event_queue", "[{\"a\":\"x\",\"b\":\"image_covered\"},{\"id\":\"ok\",\"type\":\"daily_stats\",\"occurred_at\":\"t\"}]")
        val q = EventQueue(kv)
        assertEquals(listOf("ok"), q.all().map { it.id })
        q.push(WatchmanEvent(id = "bad", type = "image_covered", occurredAt = "t"))
        q.flush { EventsResponse(listOf("ok"), emptyList(), emptyList(), listOf(RejectedEvent("bad", "unknown"))) }
        assertEquals(0, q.size)
    }

    @Test
    fun picturesAreImageViewsOrSquareDescribedLeaves() {
        // YouTube reports a channel avatar as a described Button with no text.
        assertTrue(RegionRule.isPicture("android.widget.Button", true, false, 0, false, 216, 216))
        assertTrue(RegionRule.isPicture("android.widget.ImageView", false, false, 0, false, 72, 72))
        assertFalse(RegionRule.isPicture("android.view.ViewGroup", true, false, 0, false, 984, 90))   // a text row
        assertFalse(RegionRule.isPicture("android.widget.Button", true, true, 0, false, 216, 216))    // has text
        assertFalse(RegionRule.isPicture("android.widget.Button", false, false, 0, false, 216, 216))  // no description
        assertFalse(RegionRule.isPicture("android.view.ViewGroup", true, false, 3, false, 216, 216))  // not a leaf
        assertFalse(RegionRule.isPicture("android.widget.EditText", true, false, 0, true, 216, 216))
        assertFalse(RegionRule.isPicture("android.widget.ImageView", false, false, 0, false, 12, 12))
    }

    @Test
    fun cacheFindsNearbyHashes() {
        val cache = DecisionCache()
        cache.remember(Judged(Decision.COVER, "9f3a0c1e5b7d2a46", 0.9))
        assertEquals(Decision.COVER, cache.lookup("9f3a0c1e5b7d2a47")?.decision)
        assertEquals(null, cache.lookup("0000000000000000"))
    }

    @Test
    fun queueKeepsWhatTheServerDidNotTake() {
        val q = EventQueue(MemoryKeyValue())
        for (id in listOf("a", "b", "c")) q.push(WatchmanEvent(id = id, type = "image_covered", occurredAt = "2026-11-01T00:00:00Z"))
        val wanted = q.flush { batch ->
            assertEquals(listOf("a", "b", "c"), batch.map { it.id })
            EventsResponse(listOf("a"), listOf("b"), listOf("a"))
        }
        assertEquals(listOf("a"), wanted)
        assertEquals(listOf("c"), q.all().map { it.id })
        for (i in 0 until EventQueue.MAX_QUEUE + 5) q.push(WatchmanEvent(id = "$i", type = "image_flagged", occurredAt = "2026-11-01T00:00:00Z"))
        assertEquals(EventQueue.MAX_QUEUE, q.size)
    }

    @Test
    fun yoloDecodeWithNmsAndUnletterbox() {
        val output = ModelOutput("output0", "yolov8", listOf("A", "B", "C"), null, listOf("B"))
        val anchors = 3
        val raw = FloatArray(7 * anchors)
        fun set(a: Int, vals: FloatArray) { for (r in vals.indices) raw[r * anchors + a] = vals[r] }
        set(0, floatArrayOf(100f, 100f, 40f, 40f, 0.1f, 0.9f, 0f))
        set(1, floatArrayOf(104f, 102f, 40f, 40f, 0.1f, 0.6f, 0f))
        set(2, floatArrayOf(10f, 10f, 10f, 10f, 0.2f, 0f, 0f))
        val boxes = Yolo.decode(raw, intArrayOf(1, 7, anchors), output, LetterboxInfo(0.5, 0, 20))
        assertEquals(1, boxes.size)
        assertEquals("B", boxes[0].label)
        assertEquals(160.0, boxes[0].x, 1e-6)
        assertEquals(120.0, boxes[0].y, 1e-6)
        assertEquals(0.9, Yolo.explicitScore(boxes, output), 1e-6)
        // The policy's coverage set can widen or narrow what counts.
        assertEquals(0.0, Yolo.explicitScore(boxes, output, listOf("A")), 1e-6)
        assertEquals(0.9, Yolo.explicitScore(boxes, output, listOf("A", "B")), 1e-6)
    }

    @Test
    fun tensorAndSoftmaxMatchTheManifest() {
        val input = ModelInput("input", 2, "NCHW", "RGB", 255.0, doubleArrayOf(0.5, 0.5, 0.5), doubleArrayOf(0.5, 0.5, 0.5), "bicubic")
        val argb = IntArray(4) { (0xFF shl 24) or (255 shl 16) or (0 shl 8) or 128 }
        val t = Yolo.tensor(argb, 2, input)
        assertEquals(1.0f, t[0], 1e-5f)
        assertEquals(-1.0f, t[4], 1e-5f)
        assertEquals(((128 / 255.0 - 0.5) / 0.5).toFloat(), t[8], 1e-5f)
        val out = ModelOutput("logits", "softmax", listOf("NSFW", "SFW"), "NSFW", null)
        assertTrue(Yolo.softmaxPositive(floatArrayOf(2f, 0f), out) > 0.85)
    }

    @Test
    fun policyJsonParsesSnakeCase() {
        val p = gson.fromJson("""{"version":3,"thresholds":{"cover":0.8,"flag":0.6,"detector_cover":0.55},"strict_mode":true,
            "risky_apps":[],"blocked_apps":["com.android.chrome"],"protected_browser":"org.mozilla.firefox","sample_interval_s":180,
            "whole_screen_interval_s":5,"paused_until":null,"known_bad_hashes":["9f3a0c1e5b7d2a46"]}""", Policy::class.java)
        assertEquals(0.55, p.thresholds.detectorCover, 1e-9)
        assertEquals(null, p.thresholds.ask)
        assertEquals(null, p.coverOnDevice) // absent means true
        assertEquals(listOf("com.android.chrome"), p.blockedApps)
        assertFalse(p.isPaused())
        val e = WatchmanEvent(type = "image_covered", occurredAt = Iso.now(), hasImage = true)
        assertTrue(gson.toJson(e).contains("\"has_image\":true"))
        assertTrue(gson.toJson(e).contains("\"occurred_at\""))
    }
}
