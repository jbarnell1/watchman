package app.watchman.android.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdaterTest {
    private fun file(variant: String, name: String = "watchman-android-0.3.0-$variant.apk", size: Long = 10) =
        UpdateFile(name, variant, "0".repeat(64), size, "/downloads/$name")

    private val offer = UpdateOffer("0.3.0", listOf(file("arm64-v8a"), file("armeabi-v7a")))

    @Test
    fun versionsOrderLikeTheServer() {
        assertTrue(Updater.isNewer("0.2.3", "0.2.2"))
        assertTrue(Updater.isNewer("0.3.0", "0.2.10"))
        assertTrue(Updater.isNewer("0.2.2", "0.2.2-rc.1"))
        assertTrue(Updater.isNewer("0.2.2", "0.0.0-pr.6"))
        assertFalse(Updater.isNewer("0.2.2-rc.1", "0.2.2"))
        assertFalse(Updater.isNewer("0.2.2", "0.2.2"))
        assertFalse(Updater.isNewer("0.2.1", "0.2.2"))
        assertFalse(Updater.isNewer("latest", "0.2.2"))
    }

    @Test
    fun aPhonePicksItsOwnAbiFromANewerOffer() {
        assertEquals("arm64-v8a", Updater.pick(offer, "0.2.2", listOf("arm64-v8a", "armeabi-v7a"))?.variant)
        assertEquals("armeabi-v7a", Updater.pick(offer, "0.2.2", listOf("armeabi-v7a", "armeabi"))?.variant)
        assertNull(Updater.pick(offer, "0.2.2", listOf("x86_64")))
        assertNull(Updater.pick(offer, "0.3.0", listOf("arm64-v8a")))
        assertNull(Updater.pick(offer, "0.4.0", listOf("arm64-v8a")))
        assertNull(Updater.pick(null, "0.2.2", listOf("arm64-v8a")))
    }

    @Test
    fun oddNamesAndSizesAreRefused() {
        val odd = UpdateOffer("0.3.0", listOf(file("arm64-v8a", name = "../evil.apk"), file("armeabi-v7a", size = 0)))
        assertNull(Updater.pick(odd, "0.2.2", listOf("arm64-v8a", "armeabi-v7a")))
    }
}
