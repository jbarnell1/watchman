// Sampling playing videos: one frame per whole-screen interval (the policy's
// whole_screen_interval_s, 5 s by default), dropped before any model runs when it is the same as
// the last one (ADR-023). Pure: the grab, the judge and the clock are injected so the
// logic runs under Node; content.ts wires the real canvas and the background.

import { hamming } from "./dhash";

export const SAMPLE_MS = 5000;
export const SAME_FRAME_BITS = 2;

export interface FrameGrab {
  hash: string;
  jpegBase64: string;
  width: number;
  height: number;
}

/** null: this element can never be sampled (tainted canvas, no 2d context). "later": not ready yet. */
export type Grab = (video: HTMLVideoElement) => Promise<FrameGrab | "later" | null>;

export interface SamplerDeps {
  grab: Grab;
  judge: (video: HTMLVideoElement, frame: FrameGrab) => Promise<"safe" | "cover">;
  /** Whether the element is on screen; off-screen videos are not sampled. */
  onScreen?: (video: HTMLVideoElement) => boolean;
  setInterval?: typeof setInterval;
  clearInterval?: typeof clearInterval;
}

export function isPlaying(v: HTMLVideoElement): boolean {
  return !v.paused && !v.ended && v.readyState >= 2;
}

export class VideoSampler {
  private readonly tracked = new Set<HTMLVideoElement>();
  private readonly lastHash = new WeakMap<HTMLVideoElement, string>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private ticking = false;
  /** Frames judged, frames dropped as unchanged: for the status line. */
  public judged = 0;
  public dropped = 0;

  constructor(
    private readonly deps: SamplerDeps,
    private intervalMs = SAMPLE_MS,
  ) {}

  /** The policy's interval, applied to a running timer too. */
  setIntervalMs(ms: number): void {
    const next = Math.max(1000, ms);
    if (next === this.intervalMs) return;
    this.intervalMs = next;
    if (this.timer) {
      this.stop();
      this.start();
    }
  }

  get interval(): number {
    return this.intervalMs;
  }

  track(video: HTMLVideoElement): void {
    if (video.dataset.wmVideo === "unsampleable") return;
    this.tracked.add(video);
    video.dataset.wmVideo = "tracked";
    this.start();
  }

  untrack(video: HTMLVideoElement): void {
    this.tracked.delete(video);
    if (this.tracked.size === 0) this.stop();
  }

  get size(): number {
    return this.tracked.size;
  }

  start(): void {
    if (this.timer) return;
    const si = this.deps.setInterval ?? setInterval;
    this.timer = si(() => void this.tick(), this.intervalMs);
  }

  stop(): void {
    if (!this.timer) return;
    (this.deps.clearInterval ?? clearInterval)(this.timer);
    this.timer = null;
  }

  /** One round over the tracked videos. Safe to call directly (the tests do). */
  async tick(): Promise<void> {
    if (this.ticking) return;
    this.ticking = true;
    try {
      for (const v of Array.from(this.tracked)) {
        if (!v.isConnected) {
          this.untrack(v);
          continue;
        }
        if (!isPlaying(v) || v.dataset.wm === "cover") continue;
        if (this.deps.onScreen && !this.deps.onScreen(v)) continue;
        const frame = await this.deps.grab(v);
        if (frame === "later") continue;
        if (frame === null) {
          v.dataset.wmVideo = "unsampleable";
          this.untrack(v);
          continue;
        }
        const prev = this.lastHash.get(v);
        if (prev !== undefined && hamming(prev, frame.hash) <= SAME_FRAME_BITS) {
          this.dropped++;
          continue;
        }
        this.lastHash.set(v, frame.hash);
        this.judged++;
        const decision = await this.deps.judge(v, frame);
        if (decision === "cover") v.dataset.wm = "cover";
        else if (v.dataset.wm !== "cover") v.dataset.wm = "safe";
      }
    } finally {
      this.ticking = false;
    }
  }
}
