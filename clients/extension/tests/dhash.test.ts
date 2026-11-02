import { readFileSync } from "node:fs";
import { join } from "node:path";
import { PNG } from "pngjs";
import { describe, expect, it } from "vitest";

import { dhashRgba, hamming, hashesMatch } from "../src/shared/dhash";

const VECTORS = join(__dirname, "..", "..", "..", "models", "test-vectors", "dhash");

describe("dhash", () => {
  it("reproduces every committed test vector exactly", () => {
    const data = JSON.parse(readFileSync(join(VECTORS, "vectors.json"), "utf-8")) as {
      vectors: { file: string; width: number; height: number; dhash: string }[];
    };
    expect(data.vectors.length).toBeGreaterThanOrEqual(8);
    for (const v of data.vectors) {
      const png = PNG.sync.read(readFileSync(join(VECTORS, v.file)));
      expect([png.width, png.height]).toEqual([v.width, v.height]);
      expect(dhashRgba(png.data, png.width, png.height)).toBe(v.dhash);
    }
  });

  it("hamming distance and tolerance", () => {
    expect(hamming("0000000000000000", "000000000000000f")).toBe(4);
    expect(hashesMatch("0000000000000000", "000000000000000f")).toBe(true);
    expect(hashesMatch("0000000000000000", "00000000000000ff")).toBe(false);
    expect(hamming("ffffffffffffffff", "0000000000000000")).toBe(64);
  });

  it("a flat image hashes to zero", () => {
    const rgba = new Uint8ClampedArray(20 * 20 * 4).fill(77);
    expect(dhashRgba(rgba, 20, 20)).toBe("0000000000000000");
  });
});
