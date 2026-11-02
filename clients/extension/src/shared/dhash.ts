// The image hash from docs/api-contract.md. Must reproduce models/test-vectors/dhash/vectors.json
// exactly; the TypeScript port of models/watchman_models/dhash.py.

const W = 9;
const H = 8;

/** rgba is width*height*4 bytes, row major, as ImageData gives it. */
export function dhashRgba(rgba: Uint8ClampedArray | Uint8Array, width: number, height: number): string {
  const gray = new Float64Array(width * height);
  for (let i = 0, p = 0; i < gray.length; i++, p += 4) {
    gray[i] = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2];
  }
  const small = areaAverage(gray, width, height);
  return hashFromCells(small);
}

/** Area-average downscale to 9 by 8 with fractional coverage at cell edges. */
export function areaAverage(gray: Float64Array, inW: number, inH: number): Float64Array {
  const out = new Float64Array(W * H);
  for (let oy = 0; oy < H; oy++) {
    const y0 = (oy * inH) / H;
    const y1 = ((oy + 1) * inH) / H;
    for (let ox = 0; ox < W; ox++) {
      const x0 = (ox * inW) / W;
      const x1 = ((ox + 1) * inW) / W;
      let total = 0;
      let area = 0;
      for (let y = Math.floor(y0); y < Math.min(inH, Math.ceil(y1)); y++) {
        const cy = Math.min(y + 1, y1) - Math.max(y, y0);
        if (cy <= 0) continue;
        for (let x = Math.floor(x0); x < Math.min(inW, Math.ceil(x1)); x++) {
          const cx = Math.min(x + 1, x1) - Math.max(x, x0);
          if (cx <= 0) continue;
          total += gray[y * inW + x] * cx * cy;
          area += cx * cy;
        }
      }
      out[oy * W + ox] = total / area;
    }
  }
  return out;
}

export function hashFromCells(small: Float64Array): string {
  const cells = Array.from(small, (v) => Math.floor(v + 0.5));
  let hi = 0;
  let lo = 0;
  for (let row = 0; row < H; row++) {
    for (let col = 0; col < W - 1; col++) {
      if (cells[row * W + col] > cells[row * W + col + 1]) {
        const bit = 63 - (row * 8 + col);
        if (bit >= 32) hi |= 1 << (bit - 32);
        else lo |= 1 << bit;
      }
    }
  }
  return (hi >>> 0).toString(16).padStart(8, "0") + (lo >>> 0).toString(16).padStart(8, "0");
}

export function hamming(a: string, b: string): number {
  let n = 0;
  for (let i = 0; i < 16; i += 8) {
    let x = (parseInt(a.slice(i, i + 8), 16) ^ parseInt(b.slice(i, i + 8), 16)) >>> 0;
    while (x) {
      n += x & 1;
      x >>>= 1;
    }
  }
  return n;
}

export function hashesMatch(a: string, b: string, tolerance = 6): boolean {
  return hamming(a, b) <= tolerance;
}
