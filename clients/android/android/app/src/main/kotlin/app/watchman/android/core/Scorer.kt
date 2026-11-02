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
 * ONNX Runtime with the faster of two backends per model, measured at load (ADR-034): XNNPACK with
 * [THREADS] threads, or plain CPU with [THREADS] threads. XNNPACK is quick for float models but
 * cannot run the int8 classifier's quantized operators, which then fall back to one CPU thread.
 * NNAPI was dropped: on the Pixel it ran few of these models' operators.
 * Models come verified from the household server.
 */
class OnnxScorer(classifier: ModelEntry, classifierPath: String, detector: ModelEntry?, detectorPath: String?,
                 gate: ModelEntry? = null, gatePath: String? = null, private val metrics: Metrics? = null) : Scorer, AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val classifierEntry = classifier
    private val detectorEntry = detector
    private val gateEntry = gate
    private val classifierSession: OrtSession
    private val detectorSession: OrtSession?
    private val gateSession: OrtSession?
    /** The backend each model ended up on, with its time on a plain picture. */
    val provider: String

    init {
        val plain = Bitmap.createBitmap(256, 256, Bitmap.Config.ARGB_8888).apply { eraseColor(Color.GRAY) }
        val chosen = ArrayList<String>()
        classifierSession = fastest("classify", classifierPath, chosen) { softmaxRun(it, classifier, plain) }
        detectorSession = if (detector != null && detectorPath != null) fastest("detect", detectorPath, chosen) { detectRun(it, detector, plain) } else null
        gateSession = if (gate != null && gatePath != null) fastest("gate", gatePath, chosen) { softmaxRun(it, gate, plain) } else null
        provider = chosen.joinToString(", ")
        plain.recycle()
    }

    /** Open [path] on each backend, time [WARM_RUNS] runs after a first one, keep the faster session. */
    private fun fastest(name: String, path: String, chosen: MutableList<String>, run: (OrtSession) -> Unit): OrtSession {
        var best: OrtSession? = null
        var bestMs = Double.MAX_VALUE
        var bestName = ""
        for ((label, options) in backends()) {
            val session = try { env.createSession(path, options) } catch (_: Throwable) { continue }
            try {
                run(session)  // the first run of a session is slow
                val t0 = System.nanoTime()
                repeat(WARM_RUNS) { run(session) }
                val ms = (System.nanoTime() - t0) / 1_000_000.0 / WARM_RUNS
                metrics?.time("warm_${name}_${label.substringBefore(' ')}_ms", ms)
                if (ms < bestMs) { best?.close(); best = session; bestMs = ms; bestName = label } else session.close()
            } catch (_: Throwable) {
                session.close()
            }
        }
        chosen.add("$name $bestName ${"%.0f".format(bestMs)} ms")
        return best ?: throw IllegalStateException("$name: no backend could run the model")
    }

    private fun backends(): List<Pair<String, OrtSession.SessionOptions>> {
        val out = ArrayList<Pair<String, OrtSession.SessionOptions>>()
        try {
            val x = OrtSession.SessionOptions()
            // XNNPACK keeps its own thread pool; ORT's own pool stays at one thread and does not spin.
            x.addXnnpack(mapOf("intra_op_num_threads" to THREADS.toString()))
            x.setIntraOpNumThreads(1)
            x.addConfigEntry("session.intra_op.allow_spinning", "0")
            out.add("xnnpack x$THREADS" to x)
        } catch (_: Throwable) { }
        out.add("cpu x$THREADS" to OrtSession.SessionOptions().apply { setIntraOpNumThreads(THREADS) })
        return out
    }

    override val detectorOutput: ModelOutput? get() = detectorEntry?.output
    override val hasGate: Boolean get() = gateSession != null

    override fun classify(crop: Bitmap): Double = softmaxRun(classifierSession, classifierEntry, crop)

    override fun gate(crop: Bitmap): Double {
        val session = gateSession ?: return 0.0
        return softmaxRun(session, gateEntry!!, crop)
    }

    override fun detect(crop: Bitmap): List<Box> {
        val session = detectorSession ?: return emptyList()
        return detectRun(session, detectorEntry!!, crop)
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

    private fun detectRun(session: OrtSession, entry: ModelEntry, crop: Bitmap): List<Box> {
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
