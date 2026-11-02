// Turning a score into what happens to the image, and remembering what was already judged.

import { hashesMatch } from "./dhash";
import type { Decision, Thresholds } from "./types";

export const MIN_SIDE = 20; // smaller than this is an icon, not a picture

export function decide(score: number, t: Thresholds): Decision {
  if (score >= t.cover) return "cover";
  if (score >= t.flag) return "flag";
  return "safe";
}

/** Report only (cover_on_device off): nothing is covered; what would have been is flagged instead. */
export function settle(decision: Decision, coverOnDevice: boolean): Decision {
  return !coverOnDevice && decision === "cover" ? "flag" : decision;
}

/** Between ask and cover the device covers first and lets the server's bigger model decide, when the policy allows. */
export function shouldAsk(score: number, t: Thresholds, askServer = true): boolean {
  if (!askServer) return false;
  const ask = t.ask ?? t.cover;
  return score >= ask && score < t.cover;
}

export function isTiny(width: number, height: number): boolean {
  return width < MIN_SIDE || height < MIN_SIDE;
}

/** Known-bad hashes from the policy: covered the instant they appear, on every device. */
export function isKnownBad(hash: string, knownBad: string[]): boolean {
  return knownBad.some((k) => hashesMatch(k, hash));
}

/** A bounded map that forgets its oldest entries. */
export class Lru<V> {
  private map = new Map<string, V>();
  constructor(private readonly limit: number) {}

  get(key: string): V | undefined {
    const v = this.map.get(key);
    if (v !== undefined) {
      this.map.delete(key);
      this.map.set(key, v);
    }
    return v;
  }

  set(key: string, value: V): void {
    this.map.delete(key);
    this.map.set(key, value);
    if (this.map.size > this.limit) {
      const oldest = this.map.keys().next().value as string;
      this.map.delete(oldest);
    }
  }

  get size(): number {
    return this.map.size;
  }
}

/** One piece of work per key at a time: later callers share the first caller's promise. */
export class Inflight<T> {
  private readonly map = new Map<string, Promise<T>>();

  run(key: string, work: () => Promise<T>): Promise<T> {
    const running = this.map.get(key);
    if (running) return running;
    const p = work().finally(() => this.map.delete(key));
    this.map.set(key, p);
    return p;
  }

  has(key: string): boolean {
    return this.map.has(key);
  }

  get size(): number {
    return this.map.size;
  }
}

/** At most one event per key per window: a video that stays explicit is one report a minute, not one a frame. */
export class RateLimiter {
  private readonly last = new Lru<number>(500);

  constructor(
    private readonly windowMs: number,
    private readonly now: () => number = () => Date.now(),
  ) {}

  allow(key: string): boolean {
    const t = this.now();
    const prev = this.last.get(key);
    if (prev !== undefined && t - prev < this.windowMs) return false;
    this.last.set(key, t);
    return true;
  }
}

export interface Judged {
  decision: Decision;
  hash: string;
  score: number;
}

/** Decisions by exact hash, then by near hash (Hamming 6), then by URL. */
export class DecisionCache {
  private byHash = new Lru<Judged>(4000);
  private byUrl = new Lru<Judged>(4000);

  lookup(url: string, hash?: string): Judged | undefined {
    if (hash) {
      const exact = this.byHash.get(hash);
      if (exact) return exact;
    }
    return this.byUrl.get(url);
  }

  remember(url: string, judged: Judged): void {
    this.byHash.set(judged.hash, judged);
    this.byUrl.set(url, judged);
  }
}
