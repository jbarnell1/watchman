package app.watchman.android.core

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.graphics.Bitmap
import android.graphics.Color
import java.nio.FloatBuffer

/** The on-device models behind one interface, so the pipeline can be driven by a fake. */
interface Scorer {
    /** Probability the crop is explicit, from the small-image classifier. */
    fun classify(crop: Bitmap): Double
    /** Explicit-part boxes from the detector, in crop pixels; empty when no detector is loaded. */
    fun detect(crop: Bitmap): List<Box>
    val detectorOutput: ModelOutput?
    /** True when a sorter model is loaded (ADR-034). */
    val hasGate: Boolean get() = false
    /** The sorter's suspicion for a crop. It only orders the work; it never clears a picture. */
    fun gate(crop: Bitmap): Double = 0.0
}

class FakeScorer(var classifierScore: Double = 0.0, var boxes: List<Box> = emptyList()) : Scorer {
    var calls = 0
    override fun classify(crop: Bitmap): Double { calls++; return classifierScore }
    override fun detect(crop: Bitmap): List<Box> = boxes
    override val detectorOutput: ModelOutput? = null
}

/**
 * ONNX Runtime on its XNNPACK backend with [THREADS] threads, else plain CPU (ADR-034). NNAPI was
 * dropped: on the Pixel it ran few of these models' operators and fell back to the CPU anyway.
 * Models come verified from the household server.
 */
class OnnxScorer(classifier: ModelEntry, classifierPath: String, detector: ModelEntry?, detectorPath: String?,
                 gate: ModelEntry? = null, gatePath: String? = null) : Scorer, AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val classifierEntry = classifier
    private val detectorEntry = detector
    private val gateEntry = gate
    private val classifierSession: OrtSession
    private val detectorSession: OrtSession?
    private val gateSession: OrtSession?
    val provider: String

    init {
        val (options, p) = options()
        classifierSession = env.createSession(classifierPath, options)
        detectorSession = if (detector != null && detectorPath != null) env.createSession(detectorPath, options().first) else null
        gateSession = if (gate != null && gatePath != null) env.createSession(gatePath, options().first) else null
        provider = p
    }

    private fun options(): Pair<OrtSession.SessionOptions, String> {
        val o = OrtSession.SessionOptions()
        return try {
            // XNNPACK keeps its own thread pool; ORT's own pool stays at one thread and does not spin.
            o.addXnnpack(mapOf("intra_op_num_threads" to THREADS.toString()))
            o.setIntraOpNumThreads(1)
            o.addConfigEntry("session.intra_op.allow_spinning", "0")
            o to "xnnpack x$THREADS"
        } catch (_: Throwable) {
            val cpu = OrtSession.SessionOptions()
            cpu.setIntraOpNumThreads(THREADS)
            cpu to "cpu x$THREADS"
        }
    }

    override val detectorOutput: ModelOutput? get() = detectorEntry?.output
    override val hasGate: Boolean get() = gateSession != null

    override fun classify(crop: Bitmap): Double = softmaxRun(classifierSession, classifierEntry, crop)

    override fun gate(crop: Bitmap): Double {
        val session = gateSession ?: return 0.0
        return softmaxRun(session, gateEntry!!, crop)
    }

    private fun softmaxRun(session: OrtSession, entry: ModelEntry, crop: Bitmap): Double {
        val size = entry.input.size.toLong()
        val tensor = Pixels.classifierTensor(crop, entry.input)
        val dims = when (entry.input.layout) {
            "NCHW" -> longArrayOf(1, 3, size, size)
            "CHW" -> longArrayOf(3, size, size)
            else -> longArrayOf(1, size, size, 3)
        }
        OnnxTensor.createTensor(env, FloatBuffer.wrap(tensor), dims).use { t ->
            session.run(mapOf(session.inputNames.first() to t)).use { out ->
                @Suppress("UNCHECKED_CAST")
                val logits = (out[0].value as Array<FloatArray>)[0]
                return Yolo.softmaxPositive(logits, entry.output)
            }
        }
    }

    override fun detect(crop: Bitmap): List<Box> {
        val session = detectorSession ?: return emptyList()
        val entry = detectorEntry!!
        val (tensor, lb) = Pixels.letterboxTensor(crop, entry.input)
        val size = entry.input.size.toLong()
        OnnxTensor.createTensor(env, FloatBuffer.wrap(tensor), longArrayOf(1, 3, size, size)).use { t ->
            session.run(mapOf(session.inputNames.first() to t)).use { out ->
                @Suppress("UNCHECKED_CAST")
                val value = out[0].value as Array<Array<FloatArray>>
                val rows = value[0].size
                val anchors = value[0][0].size
                val flat = FloatArray(rows * anchors)
                for (r in 0 until rows) System.arraycopy(value[0][r], 0, flat, r * anchors, anchors)
                return Yolo.decode(flat, intArrayOf(1, rows, anchors), entry.output, lb)
            }
        }
    }

    /**
     * Run each model a few times on a plain picture: the first run of a session is slow, and the
     * steady times go to the metrics so the Devices page shows what this phone really does.
     */
    fun warmUp(metrics: Metrics) {
        val plain = Bitmap.createBitmap(256, 256, Bitmap.Config.ARGB_8888).apply { eraseColor(Color.GRAY) }
        repeat(WARM_RUNS) {
            metrics.timed("warm_classify_ms") { classify(plain) }
            if (gateSession != null) metrics.timed("warm_gate_ms") { gate(plain) }
            if (detectorSession != null) metrics.timed("warm_detect_ms") { detect(plain) }
        }
        plain.recycle()
    }

    override fun close() {
        classifierSession.close()
        detectorSession?.close()
        gateSession?.close()
    }

    companion object {
        const val THREADS = 4
        const val WARM_RUNS = 3
    }
}
