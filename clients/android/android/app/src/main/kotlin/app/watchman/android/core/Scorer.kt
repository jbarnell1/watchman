package app.watchman.android.core

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.graphics.Bitmap
import java.nio.FloatBuffer

/** The two on-device models behind one interface, so the pipeline can be driven by a fake. */
interface Scorer {
    /** Probability the crop is explicit, from the small-image classifier. */
    fun classify(crop: Bitmap): Double
    /** Explicit-part boxes from the detector, in crop pixels; empty when no detector is loaded. */
    fun detect(crop: Bitmap): List<Box>
    val detectorOutput: ModelOutput?
}

class FakeScorer(var classifierScore: Double = 0.0, var boxes: List<Box> = emptyList()) : Scorer {
    var calls = 0
    override fun classify(crop: Bitmap): Double { calls++; return classifierScore }
    override fun detect(crop: Bitmap): List<Box> = boxes
    override val detectorOutput: ModelOutput? = null
}

/** ONNX Runtime with NNAPI when available, else CPU. Models come verified from the household server. */
class OnnxScorer(classifier: ModelEntry, classifierPath: String, detector: ModelEntry?, detectorPath: String?) : Scorer, AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val classifierEntry = classifier
    private val detectorEntry = detector
    private val classifierSession: OrtSession
    private val detectorSession: OrtSession?
    val provider: String

    init {
        var p = "cpu"
        val options = OrtSession.SessionOptions()
        try { options.addNnapi(); p = "nnapi" } catch (_: Throwable) { }
        options.setIntraOpNumThreads(2)
        classifierSession = env.createSession(classifierPath, options)
        detectorSession = if (detector != null && detectorPath != null) env.createSession(detectorPath, OrtSession.SessionOptions().apply { setIntraOpNumThreads(2) }) else null
        provider = p
    }

    override val detectorOutput: ModelOutput? get() = detectorEntry?.output

    override fun classify(crop: Bitmap): Double {
        val size = classifierEntry.input.size
        val tensor = Pixels.classifierTensor(crop, classifierEntry.input)
        val dims = if (classifierEntry.input.layout == "NCHW") longArrayOf(1, 3, size.toLong(), size.toLong()) else longArrayOf(1, size.toLong(), size.toLong(), 3)
        OnnxTensor.createTensor(env, FloatBuffer.wrap(tensor), dims).use { t ->
            classifierSession.run(mapOf(classifierSession.inputNames.first() to t)).use { out ->
                val logits = (out[0].value as Array<FloatArray>)[0]
                return Yolo.softmaxPositive(logits, classifierEntry.output)
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
                val value = out[0].value as Array<Array<FloatArray>>
                val rows = value[0].size
                val anchors = value[0][0].size
                val flat = FloatArray(rows * anchors)
                for (r in 0 until rows) System.arraycopy(value[0][r], 0, flat, r * anchors, anchors)
                return Yolo.decode(flat, intArrayOf(1, rows, anchors), entry.output, lb)
            }
        }
    }

    override fun close() {
        classifierSession.close()
        detectorSession?.close()
    }
}
