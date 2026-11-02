// The crop classifier behind one interface: ONNX Runtime Web, or a fake for tests.

import type { ModelEntry } from "../shared/types";

export interface Scorer {
  /** The model's square input size in pixels. */
  readonly size: number;
  /** rgba is size*size*4 bytes. Returns the probability that the image is explicit. */
  score(rgba: Uint8ClampedArray): Promise<number>;
}

export class FakeScorer implements Scorer {
  readonly size = 32;
  constructor(public fixed = 0) {}
  async score(): Promise<number> {
    return this.fixed;
  }
}

/** Builds the model's input tensor from square RGBA pixels, as the manifest says. */
export function toTensor(rgba: Uint8ClampedArray, entry: ModelEntry): Float32Array {
  const { size, scale, mean, std, layout, color } = entry.input;
  const n = size * size;
  const out = new Float32Array(3 * n);
  const order = color === "BGR" ? [2, 1, 0] : [0, 1, 2];
  for (let i = 0; i < n; i++) {
    for (let c = 0; c < 3; c++) {
      const v = (rgba[i * 4 + order[c]] / scale - mean[c]) / std[c];
      if (layout === "NCHW") out[c * n + i] = v;
      else out[i * 3 + c] = v;
    }
  }
  return out;
}

export function positiveProbability(logits: Float32Array | number[], entry: ModelEntry): number {
  const labels = entry.output.labels;
  const idx = labels.indexOf(entry.output.positive ?? labels[0]);
  const arr = Array.from(logits);
  if (entry.output.kind === "sigmoid") return 1 / (1 + Math.exp(-arr[idx]));
  const max = Math.max(...arr);
  const exps = arr.map((v) => Math.exp(v - max));
  const sum = exps.reduce((a, b) => a + b, 0);
  return exps[idx] / sum;
}

export class OnnxScorer implements Scorer {
  readonly size: number;
  private constructor(
    private readonly session: { run(feeds: Record<string, unknown>): Promise<Record<string, { data: Float32Array }>> },
    private readonly entry: ModelEntry,
    private readonly makeTensor: (data: Float32Array, dims: number[]) => unknown,
    private readonly inputName: string,
  ) {
    this.size = entry.input.size;
  }

  static async create(modelBytes: ArrayBuffer, entry: ModelEntry, wasmBaseUrl: string): Promise<OnnxScorer> {
    const ort = await import("onnxruntime-web");
    ort.env.wasm.wasmPaths = wasmBaseUrl;
    ort.env.wasm.numThreads = 1;
    const session = await ort.InferenceSession.create(modelBytes, { executionProviders: ["wasm"] });
    const inputName = session.inputNames[0];
    return new OnnxScorer(
      session as unknown as OnnxScorer["session"],
      entry,
      (data, dims) => new ort.Tensor("float32", data, dims),
      inputName,
    );
  }

  async score(rgba: Uint8ClampedArray): Promise<number> {
    const { size, layout } = this.entry.input;
    const dims = layout === "NCHW" ? [1, 3, size, size] : [1, size, size, 3];
    const out = await this.session.run({ [this.inputName]: this.makeTensor(toTensor(rgba, this.entry), dims) });
    const first = Object.values(out)[0];
    return positiveProbability(first.data, this.entry);
  }
}
