package app.watchman.android.touch

import android.os.Binder
import android.os.Parcel
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import app.watchman.android.core.Touch
import app.watchman.android.core.TouchDecoder
import java.io.DataOutputStream
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Runs in a separate process with shell rights, started by Shizuku (ADR-044). It reads the
 * touchscreen's input device, which an app may not open, and writes one finger's down, move and
 * up into a pipe the app reads. It only reads: it never injects, records or stores touches.
 * Plain binder transactions keep it free of AIDL; [FingerFeed] is the other end.
 */
class TouchService : Binder() {
    @Volatile private var reader: Thread? = null

    override fun onTransact(code: Int, data: Parcel, reply: Parcel?, flags: Int): Boolean {
        when (code) {
            OPEN -> {
                val device = findDevice()
                if (device == null) {
                    reply?.writeInt(0)
                    reply?.writeString("no touchscreen found")
                    return true
                }
                val pipe = ParcelFileDescriptor.createPipe()
                reader?.interrupt()
                reader = Thread({ pump(device, pipe[1]) }, "watchman-touch").apply { isDaemon = true; start() }
                reply?.writeInt(1)
                reply?.writeInt(device.maxX)
                reply?.writeInt(device.maxY)
                pipe[0].writeToParcel(reply!!, android.os.Parcelable.PARCELABLE_WRITE_RETURN_VALUE)
                return true
            }
            DESTROY -> { System.exit(0); return true }
        }
        return super.onTransact(code, data, reply, flags)
    }

    private fun findDevice(): Touch.Device? = try {
        val p = ProcessBuilder("getevent", "-pl").redirectErrorStream(true).start()
        val text = p.inputStream.bufferedReader().readText()
        p.waitFor()
        Touch.findDevice(text)
    } catch (_: Exception) { null }

    /** Copy decoded samples into the pipe until the app closes its end. */
    private fun pump(device: Touch.Device, sink: ParcelFileDescriptor) {
        // struct input_event: a timeval (two longs) then type, code and value.
        val size = if (android.os.Process.is64Bit()) 24 else 16
        val decoder = TouchDecoder()
        try {
            FileInputStream(device.path).use { input ->
                DataOutputStream(ParcelFileDescriptor.AutoCloseOutputStream(sink)).use { out ->
                    val buf = ByteArray(size * 64)
                    val bb = ByteBuffer.wrap(buf).order(ByteOrder.nativeOrder())
                    var filled = 0
                    while (!Thread.currentThread().isInterrupted) {
                        val n = input.read(buf, filled, buf.size - filled)
                        if (n < 0) break
                        filled += n
                        var off = 0
                        while (filled - off >= size) {
                            val type = bb.getShort(off + size - 8).toInt() and 0xffff
                            val code = bb.getShort(off + size - 6).toInt() and 0xffff
                            val value = bb.getInt(off + size - 4)
                            off += size
                            val s = decoder.feed(type, code, value, SystemClock.uptimeMillis()) ?: continue
                            out.writeByte(s.kind); out.writeInt(s.x); out.writeInt(s.y); out.writeLong(s.tMs)
                            out.flush()
                        }
                        System.arraycopy(buf, off, buf, 0, filled - off)
                        filled -= off
                    }
                }
            }
        } catch (_: Exception) {
            // The app closed the pipe or the device went away; the app falls back to scroll events.
        }
    }

    companion object {
        const val OPEN = FIRST_CALL_TRANSACTION
        /** Shizuku's reserved transaction for stopping a user service. */
        const val DESTROY = 16777115
    }
}
