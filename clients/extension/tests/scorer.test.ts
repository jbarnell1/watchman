import { describe, expect, it } from "vitest";

import { positiveProbability, toTensor } from "../src/engine/scorer";
import type { ModelEntry } from "../src/shared/types";

const entry: ModelEntry = {
  role: "crop_classifier", file: "x.onnx", url: "", sha256: "", bytes: 0,
  input: { name: "input", size: 2, layout: "NCHW", color: "RGB", scale: 255, mean: [0.5, 0.5, 0.5], std: [0.5, 0.5, 0.5], resize: "bicubic" },
  output: { name: "logits", kind: "softmax", labels: ["NSFW", "SFW"], positive: "NSFW" },
};

describe("scorer maths", () => {
  it("builds the normalised NCHW tensor the manifest describes", () => {
    const rgba = new Uint8ClampedArray([255, 0, 128, 255, 255, 0, 128, 255, 255, 0, 128, 255, 255, 0, 128, 255]);
    const t = toTensor(rgba, entry);
    expect(t.length).toBe(12);
    expect(t[0]).toBeCloseTo(1.0);          // R plane
    expect(t[4]).toBeCloseTo(-1.0);         // G plane
    expect(t[8]).toBeCloseTo((128 / 255 - 0.5) / 0.5, 5);
  });

  it("BGR and NHWC layouts reorder as asked", () => {
    const e = { ...entry, input: { ...entry.input, color: "BGR" as const, layout: "NHWC" as const } };
    const rgba = new Uint8ClampedArray([255, 0, 0, 255, 255, 0, 0, 255, 255, 0, 0, 255, 255, 0, 0, 255]);
    const t = toTensor(rgba, e);
    expect(t[0]).toBeCloseTo(-1.0); // first pixel, B channel first
    expect(t[2]).toBeCloseTo(1.0);  // then R
  });

  it("softmax picks the positive label whatever its position", () => {
    expect(positiveProbability([2, 0], entry)).toBeGreaterThan(0.85);
    const flipped = { ...entry, output: { ...entry.output, labels: ["SFW", "NSFW"] } };
    expect(positiveProbability([2, 0], flipped)).toBeLessThan(0.15);
    const sig = { ...entry, output: { ...entry.output, kind: "sigmoid" as const, labels: ["NSFW"] } };
    expect(positiveProbability([0], sig)).toBeCloseTo(0.5);
  });
});
