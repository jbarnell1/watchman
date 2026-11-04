package app.watchman.android.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtectionTest {
    private val min = 60_000L
    private val t0 = 1_800_000_000_000L

    @Test
    fun noGapWithoutAnAliveMarkOrWithinTwoMinutes() {
        assertNull(ProtectionGap.decide(null, false, t0, bootedAt = 0))
        assertNull(ProtectionGap.decide(t0, false, t0 + 2 * min, bootedAt = 0))
    }

    @Test
    fun killedWhenThePhoneDidNotReboot() {
        val gap = ProtectionGap.decide(t0, false, t0 + 10 * min, bootedAt = t0 - 60 * min)
        assertNotNull(gap)
        assertEquals("killed", gap!!.reason)
        assertEquals(t0, gap.fromMillis)
        assertEquals(t0 + 10 * min, gap.toMillis)
    }

    @Test
    fun rebootWhenThePhoneStartedAfterTheLastMark() {
        val gap = ProtectionGap.decide(t0, false, t0 + 5 * min, bootedAt = t0 + 3 * min)
        assertEquals("reboot", gap!!.reason)
    }

    @Test
    fun aPartnerPauseIsNotAGap() {
        // Stopped cleanly inside a pause that is still running: quiet.
        assertNull(ProtectionGap.decide(t0, true, t0 + 20 * min, bootedAt = 0, pausedUntil = t0 + 30 * min))
        // Killed inside a pause, back within two minutes of its end: quiet.
        assertNull(ProtectionGap.decide(t0, false, t0 + 31 * min, bootedAt = 0, pausedUntil = t0 + 30 * min))
        // Back long after the pause ended: only the time after it is a gap, even after a clean stop.
        val gap = ProtectionGap.decide(t0, true, t0 + 50 * min, bootedAt = 0, pausedUntil = t0 + 30 * min)
        assertEquals(t0 + 30 * min, gap!!.fromMillis)
    }

    @Test
    fun aCleanStopWithoutAPauseIsQuiet() {
        assertNull(ProtectionGap.decide(t0, true, t0 + 60 * min, bootedAt = 0))
    }

    @Test
    fun theGapEventMatchesTheContract() {
        val e = ProtectionGap.event(ProtectionGap.Gap(t0, t0 + 10 * min, "killed"))
        assertEquals("protection_gap", e.type)
        assertEquals("none", e.action)
        assertEquals(Iso.format(t0 + 10 * min), e.occurredAt)
        assertEquals(Iso.format(t0), e.detail["from"])
        assertEquals(Iso.format(t0 + 10 * min), e.detail["to"])
        assertEquals("killed", e.detail["reason"])
        assertTrue(e.id.isNotBlank())
    }

    @Test
    fun secureTimeGoesToTheAppInFront() {
        var now = t0
        val s = SecureApps({ now })
        s.seen("com.bank")
        now += 5_000; s.seen("com.bank")
        now += 5_000; s.clear() // a frame arrived
        now += 30_000
        s.seen("com.other")
        now += 2_000; s.seen("com.bank") // switching closes the other stretch
        now += 3_000
        val out = s.take()
        assertEquals(mapOf("com.bank" to 13L, "com.other" to 2L), out)
        // Still in front: the open stretch carries on into the next window.
        now += 4_000; s.clear()
        assertEquals(mapOf("com.bank" to 4L), s.take())
        assertTrue(s.take().isEmpty())
    }

    @Test
    fun anUnconfirmedSecureStretchCountsOnlyTwoMinutes() {
        var now = t0
        val s = SecureApps({ now })
        s.seen("com.bank")
        now += 60 * min
        assertEquals(mapOf("com.bank" to SecureApps.STALE_MS / 1000), s.take())
    }

    @Test
    fun secureAppsAreCappedAndGivenBackOnFailure() {
        var now = t0
        val s = SecureApps({ now })
        for (i in 0 until 70) { s.seen("app.$i"); now += (i + 1) * 1000L }
        s.clear()
        val first = s.take()
        assertEquals(SecureApps.MAX_PACKAGES, first.size)
        assertEquals(70L, first["app.69"]) // longest first
        s.giveBack(first)
        val again = s.take()
        assertEquals(first, again)
        assertEquals(6, s.take().size) // the shortest six waited for the next heartbeat
    }

    @Test
    fun judgeTallyCountsScreenTimeAndGivesBack() {
        var now = t0
        val j = JudgeTally { now }
        j.screenOn()
        now += 100_000; j.judged()
        now += 20_000; j.screenOff()
        now += 180_000
        val w = j.take()
        assertEquals(300L, w.windowS)
        assertEquals(120L, w.screenOnS)
        assertEquals(1L, w.judged)
        assertEquals(Iso.format(t0 + 100_000), w.toMap()["last_judged_at"])
        j.giveBack(w) // the heartbeat failed
        j.screenOn()
        now += 60_000
        val next = j.take()
        assertEquals(360L, next.windowS)
        assertEquals(180L, next.screenOnS)
        assertEquals(1L, next.judged)
        assertNull(JudgeTally { now }.take().toMap()["last_judged_at"])
    }

    @Test
    fun unwatchedCountsOnlyScreenOnTimeWithAReasonAndGivesBack() {
        var now = t0
        val j = JudgeTally { now }
        j.unwatched(JudgeTally.PAUSED, true) // screen still off: not counted
        now += 60_000
        j.screenOn()
        now += 30_000 // paused, screen on
        j.unwatched(JudgeTally.EXEMPT, true)
        now += 10_000
        j.unwatched(JudgeTally.PAUSED, false) // still exempt
        now += 20_000
        j.unwatched(JudgeTally.EXEMPT, false)
        now += 40_000 // watched
        val w = j.take()
        assertEquals(160L, w.windowS)
        assertEquals(100L, w.screenOnS)
        assertEquals(60L, w.unwatchedS)
        assertEquals(60L, w.toMap()["unwatched_s"])
        j.giveBack(w)
        assertEquals(60L, j.take().unwatchedS)
    }

    @Test
    fun aSecureWindowIsUnwatchedTime() {
        var now = t0
        val j = JudgeTally { now }
        val s = SecureApps({ now }) { j.unwatched(JudgeTally.SECURE, it) }
        j.screenOn()
        s.seen("com.bank")
        now += 30_000
        s.clear()
        now += 30_000
        val w = j.take()
        assertEquals(60L, w.screenOnS)
        assertEquals(30L, w.unwatchedS)
    }

    @Test
    fun withNoPolicyProtectionUsesTheDefaults() {
        val p = Policy.DEFAULTS
        assertEquals(WatchLevel.REALTIME, p.levelOf("com.example"))
        assertTrue(p.blockedApps.isEmpty())
        assertTrue(p.thresholds.cover < 1.0)
        assertTrue(!p.isPaused())
    }

    @Test
    fun debuggingIsUnknownUntilACableShowsIt() {
        // Android hides the settings from apps: 0 is not "off".
        assertEquals(mapOf("developer_options" to null, "usb_debugging" to null), DebugFlags.decide(0, 0, null))
        // A cable with adb on: both on, and remembered after the cable comes out.
        val seen = DebugFlags.remember(DebugFlags.Usb(connected = true, adb = true), null)
        assertEquals(true, DebugFlags.remember(DebugFlags.Usb(connected = false, adb = false), seen))
        assertEquals(mapOf("developer_options" to true, "usb_debugging" to true), DebugFlags.decide(0, 0, seen))
        // A cable without adb: debugging is off; developer options stay unknown.
        val off = DebugFlags.remember(DebugFlags.Usb(connected = true, adb = false), seen)
        assertEquals(mapOf("developer_options" to null, "usb_debugging" to false), DebugFlags.decide(0, 0, off))
        // Where the settings can be read (older Android, privileged builds), a 1 wins.
        assertEquals(mapOf("developer_options" to true, "usb_debugging" to true), DebugFlags.decide(1, 1, false))
    }
}
