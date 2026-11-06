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
 * ONNX Runtime with the cheapest of three backends per model, measured at load (ADR-034, ADR-056, ADR-057):
 * XNNPACK with [THREADS] threads, plain CPU with [THREADS] threads, or plain CPU with [FEW_THREADS]. XNNPACK is quick for float models but
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
    private var gateSession: OrtSession?
    /** Each model's time on a plain picture with its chosen backend. */
    private val warmMs = HashMap<String, Double>()
    /** The backend each model ended up on, with its time on a plain picture. */
    val provider: String

    init {
        val plain = Bitmap.createBitmap(256, 256, Bitmap.Config.ARGB_8888).apply { eraseColor(Color.GRAY) }
        val chosen = ArrayList<String>()
        classifierSession = fastest("classify", classifierPath, chosen) { softmaxRun(it, classifier, plain) }
        detectorSession = if (detector != null && detectorPath != null) fastest("detect", detectorPath, chosen) { detectRun(it, detector, plain) } else null
        gateSession = if (gate != null && gatePath != null) fastest("gate", gatePath, chosen) { softmaxRun(it, gate, plain) } else null
        // Sorting costs one sorter run for every new picture before the first classifier run, so it
        // only pays when the sorter is several times cheaper. On the owner's Pixel it was about half
        // the classifier's time, and sorting made the first cover later on average (ADR-034).
        if (gateSession != null && warmMs["gate"]!! * GATE_WORTH > warmMs["classify"]!!) {
            gateSession?.close()
            gateSession = null
            chosen.add("sorter off: not ${GATE_WORTH}x cheaper than the classifier")
            metrics?.count("gate_off")
        }
        provider = chosen.joinToString(", ")
        plain.recycle()
    }

    /**
     * Open [path] on each backend and time [WARM_RUNS] runs after a first one. Keep the one that costs
     * the least CPU time across all its threads (what the battery pays for), unless it is more than
     * [SLOWER_OK] times slower than the quickest: the plain CPU backend ran two to three cores per run
     * for about the same wall time as XNNPACK on one. The margin is small because load-time runs on a
     * quiet phone flatter the slower backend: XNNPACK measured under 1.5 times slower for the
     * classifier at load and ran 2.1 times slower in a day of use, which doubled the time to the first
     * cover (ADR-057).
     */
    private fun fastest(name: String, path: String, chosen: MutableList<String>, run: (OrtSession) -> Unit): OrtSession {
        class Tried(val session: OrtSession, val label: String, val ms: Double, val cpuMs: Double)
        val tried = ArrayList<Tried>()
        for ((key, label, options) in backends()) {
            val session = try { env.createSession(path, options) } catch (_: Throwable) { continue }
            try {
                run(session)  // the first run of a session is slow
                val t0 = System.nanoTime()
                val cpu0 = android.os.Process.getElapsedCpuTime()
                repeat(WARM_RUNS) { run(session) }
                val ms = (System.nanoTime() - t0) / 1_000_000.0 / WARM_RUNS
                val cpuMs = (android.os.Process.getElapsedCpuTime() - cpu0).toDouble() / WARM_RUNS
                metrics?.time("warm_${name}_${key}_ms", ms)
                metrics?.time("warm_${name}_${key}_cpu_ms", cpuMs)
                tried.add(Tried(session, label, ms, cpuMs))
            } catch (_: Throwable) {
                session.close()
            }
        }
        val quickest = tried.minOfOrNull { it.ms } ?: throw IllegalStateException("$name: no backend could run the model")
        val best = tried.filter { it.ms <= quickest * SLOWER_OK }.minBy { it.cpuMs }
        tried.filter { it !== best }.forEach { it.session.close() }
        chosen.add("$name ${best.label} ${"%.0f".format(best.ms)} ms, cpu ${"%.0f".format(best.cpuMs)} ms")
        warmMs[name] = best.ms
        return best.session
    }

    /** [key] names the backend in warm-up metrics; the four-thread ones keep their earlier names. */
    private data class Backend(val key: String, val label: String, val options: OrtSession.SessionOptions)

    private fun backends(): List<Backend> {
        val out = ArrayList<Backend>()
        try {
            val x = OrtSession.SessionOptions()
            // XNNPACK keeps its own thread pool; ORT's own pool stays at one thread and does not spin.
            x.addXnnpack(mapOf("intra_op_num_threads" to THREADS.toString()))
            x.setIntraOpNumThreads(1)
            x.addConfigEntry("session.intra_op.allow_spinning", "0")
            out.add(Backend("xnnpack", "xnnpack x$THREADS", x))
        } catch (_: Throwable) { }
        // Spinning keeps worker threads busy-waiting between runs: barely faster, and on the Pixel it
        // was most of the app's battery use.
        for ((key, threads) in listOf("cpu" to THREADS, "cpu$FEW_THREADS" to FEW_THREADS)) {
            out.add(Backend(key, "cpu x$threads", OrtSession.SessionOptions().apply {
                setIntraOpNumThreads(threads)
                addConfigEntry("session.intra_op.allow_spinning", "0")
                addConfigEntry("session.inter_op.allow_spinning", "0")
            }))
        }
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
        /** Fewer threads on plain CPU: less overhead per run than four, quicker than XNNPACK's int8 fallback. */
        const val FEW_THREADS = 2
        const val WARM_RUNS = 3
        /** How much slower than the quickest backend a cheaper one may be and still be chosen. */
        const val SLOWER_OK = 1.25
        /** The sorter is used only when it is at least this many times cheaper than the classifier. */
        const val GATE_WORTH = 4
    }
}
