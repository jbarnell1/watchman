// Fetch an image, decode it, hash it, score it, and keep a small JPEG for the server if asked.
// Runs where a canvas exists: Chrome's offscreen document, or an iframe in Firefox's background page.

import { base64ToBytes, blobToBase64 } from "../shared/bytes";
import { Lru } from "../shared/decide";
import { dhashRgba } from "../shared/dhash";
import type { ScoreFailure, ScoreResult } from "../shared/types";
import type { Scorer } from "./scorer";

export const MAX_DECODE_SIDE = 1024;

export interface Decoded {
  width: number;
  height: number;
  rgba: Uint8ClampedArray;
  bitmap: ImageBitmap;
}

export async function fetchAndDecode(url: string, fetchImpl: typeof fetch = fetch): Promise<Decoded> {
  const res = await fetchImpl(url, { credentials: "include", cache: "force-cache" });
  if (!res.ok) throw new Error(`fetch ${res.status}`);
  return decodeBlob(await res.blob());
}

export async function decodeBlob(blob: Blob): Promise<Decoded> {
  const bitmap = await createImageBitmap(blob);
  const scale = Math.min(1, MAX_DECODE_SIDE / Math.max(bitmap.width, bitmap.height));
  const w = Math.max(1, Math.round(bitmap.width * scale));
  const h = Math.max(1, Math.round(bitmap.height * scale));
  const rgba = drawToPixels(bitmap, w, h);
  return { width: bitmap.width, height: bitmap.height, rgba, bitmap };
}

export function drawToPixels(bitmap: ImageBitmap, w: number, h: number): Uint8ClampedArray {
  const canvas = new OffscreenCanvas(w, h);
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(bitmap, 0, 0, w, h);
  return ctx.getImageData(0, 0, w, h).data;
}

export async function toJpeg(bitmap: ImageBitmap, maxSide: number, quality = 0.85): Promise<Blob> {
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  const w = Math.max(1, Math.round(bitmap.width * scale));
  const h = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = new OffscreenCanvas(w, h);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, w, h);
  return canvas.convertToBlob({ type: "image/jpeg", quality });
}

export { blobToBase64 };

export class Engine {
  /** Pixels seen before, by exact hash: a CDN variant of a scored picture costs no inference (ADR-023). */
  private readonly scores = new Lru<number>(2000);
  public cacheHits = 0;

  constructor(
    private readonly scorer: Scorer,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  async score(url: string, maxSide: number, wantImage: boolean): Promise<ScoreResult | ScoreFailure> {
    try {
      const f = this.fetchImpl;
      return await this.judge(await fetchAndDecode(url, f), maxSide, wantImage);
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    }
  }

  /** A JPEG the content script already made (a video frame). It is kept as the upload copy. */
  async scoreBytes(jpegBase64: string): Promise<ScoreResult | ScoreFailure> {
    try {
      const blob = new Blob([base64ToBytes(jpegBase64)], { type: "image/jpeg" });
      const r = await this.judge(await decodeBlob(blob), 0, false);
      return r.ok ? { ...r, jpegBase64 } : r;
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    }
  }

  private async judge(decoded: Decoded, maxSide: number, wantImage: boolean): Promise<ScoreResult> {
    const t0 = performance.now();
    const hash = dhashRgba(decoded.rgba, Math.min(decoded.width, MAX_DECODE_SIDE), Math.min(decoded.height, MAX_DECODE_SIDE));
    let score = this.scores.get(hash);
    const cached = score !== undefined;
    if (score === undefined) {
      score = await this.scorer.score(drawToPixels(decoded.bitmap, this.scorer.size, this.scorer.size));
      this.scores.set(hash, score);
    } else {
      this.cacheHits++;
    }
    let jpegBase64: string | null = null;
    if (wantImage) jpegBase64 = await blobToBase64(await toJpeg(decoded.bitmap, maxSide));
    decoded.bitmap.close();
    return { ok: true, hash, score, width: decoded.width, height: decoded.height, jpegBase64,
      ms: Math.round((performance.now() - t0) * 10) / 10, cached };
  }
}
