package app.watchman.android.core

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import java.io.ByteArrayOutputStream

/** Bitmap helpers that feed the pure-Kotlin maths in DHash and Yolo. */
object Pixels {
    fun argb(bitmap: Bitmap): IntArray {
        val out = IntArray(bitmap.width * bitmap.height)
        bitmap.getPixels(out, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        return out
    }

    fun dhashWithDetail(bitmap: Bitmap): DHash.Hashed {
        val b = if (bitmap.width * bitmap.height > 512 * 512) Bitmap.createScaledBitmap(bitmap, 512, 512 * bitmap.height / bitmap.width, true) else bitmap
        return DHash.computeWithDetail(argb(b), b.width, b.height)
    }

    fun dhash(bitmap: Bitmap): String {
        val b = if (bitmap.width * bitmap.height > 512 * 512) Bitmap.createScaledBitmap(bitmap, 512, 512 * bitmap.height / bitmap.width, true) else bitmap
        return DHash.compute(argb(b), b.width, b.height)
    }

    /** Resize the whole crop to size by size (classifier entries). */
    fun classifierTensor(bitmap: Bitmap, input: ModelInput): FloatArray {
        val square = Bitmap.createScaledBitmap(bitmap, input.size, input.size, true)
        return Yolo.tensor(argb(square), input.size, input)
    }

    /** Letterbox into size by size with 114 gray (detector entries). */
    fun letterboxTensor(bitmap: Bitmap, input: ModelInput): Pair<FloatArray, LetterboxInfo> {
        val size = input.size
        val scale = minOf(size.toDouble() / bitmap.width, size.toDouble() / bitmap.height)
        val nw = maxOf(1, Math.round(bitmap.width * scale).toInt())
        val nh = maxOf(1, Math.round(bitmap.height * scale).toInt())
        val padX = (size - nw) / 2
        val padY = (size - nh) / 2
        val canvas = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val c = Canvas(canvas)
        c.drawColor(Color.rgb(114, 114, 114))
        c.drawBitmap(bitmap, null, Rect(padX, padY, padX + nw, padY + nh), Paint(Paint.FILTER_BITMAP_FLAG))
        return Yolo.tensor(argb(canvas), size, input) to LetterboxInfo(scale, padX, padY)
    }

    fun crop(bitmap: Bitmap, r: Rect): Bitmap {
        val x = r.left.coerceIn(0, bitmap.width - 1)
        val y = r.top.coerceIn(0, bitmap.height - 1)
        val w = r.width().coerceIn(1, bitmap.width - x)
        val h = r.height().coerceIn(1, bitmap.height - y)
        return Bitmap.createBitmap(bitmap, x, y, w, h)
    }

    fun jpeg(bitmap: Bitmap, maxSide: Int = 512, quality: Int = 85): ByteArray {
        val scale = minOf(1.0, maxSide.toDouble() / maxOf(bitmap.width, bitmap.height))
        val b = if (scale < 1) Bitmap.createScaledBitmap(bitmap, (bitmap.width * scale).toInt(), (bitmap.height * scale).toInt(), true) else bitmap
        val out = ByteArrayOutputStream()
        b.compress(Bitmap.CompressFormat.JPEG, quality, out)
        return out.toByteArray()
    }

    /** Heavy pixelation for an overlay patch: colour and layout stay, detail goes. */
    fun pixelate(bitmap: Bitmap, w: Int, h: Int): Bitmap {
        val tiny = Bitmap.createScaledBitmap(bitmap, maxOf(2, w / 24), maxOf(2, h / 24), true)
        return Bitmap.createScaledBitmap(tiny, w, h, false)
    }
}
