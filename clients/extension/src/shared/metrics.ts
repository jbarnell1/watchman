// Work metrics (ADR-025): counters and timing histograms, merged by adding. Buckets hold samples at
// or below each bound in BOUNDS_MS, the last everything slower. Counts and milliseconds only.

export const BOUNDS_MS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024];
export const BUCKETS = BOUNDS_MS.length + 1;

export interface Timing {
  n: number;
  sum: number;
  max: number;
  buckets: number[];
}

export interface Snapshot {
  window_s: number;
  counters: Record<string, number>;
  timings: Record<string, Timing>;
}

export function emptySnapshot(): Snapshot {
  return { window_s: 0, counters: {}, timings: {} };
}

export function bucketOf(ms: number): number {
  for (let i = 0; i < BOUNDS_MS.length; i++) if (ms <= BOUNDS_MS[i]) return i;
  return BOUNDS_MS.length;
}

export function isEmpty(s: Snapshot): boolean {
  return Object.keys(s.counters).length === 0 && Object.keys(s.timings).length === 0;
}

export function count(s: Snapshot, name: string, n = 1): void {
  if (n) s.counters[name] = (s.counters[name] ?? 0) + n;
}

export function time(s: Snapshot, name: string, ms: number): void {
  const t = (s.timings[name] ??= { n: 0, sum: 0, max: 0, buckets: new Array(BUCKETS).fill(0) });
  t.n += 1;
  t.sum += ms;
  if (ms > t.max) t.max = ms;
  t.buckets[bucketOf(ms)] += 1;
}

export function merge(a: Snapshot, b: Snapshot): Snapshot {
  const out: Snapshot = {
    window_s: a.window_s + b.window_s,
    counters: { ...a.counters },
    timings: Object.fromEntries(Object.entries(a.timings).map(([k, t]) => [k, { ...t, buckets: [...t.buckets] }])),
  };
  for (const [k, v] of Object.entries(b.counters)) out.counters[k] = (out.counters[k] ?? 0) + v;
  for (const [k, t] of Object.entries(b.timings)) {
    const have = out.timings[k];
    if (!have) {
      out.timings[k] = { ...t, buckets: [...t.buckets] };
      continue;
    }
    have.n += t.n;
    have.sum += t.sum;
    have.max = Math.max(have.max, t.max);
    have.buckets = have.buckets.map((x, i) => x + (t.buckets[i] ?? 0));
  }
  return out;
}

/** The bucket bound at or below which a share q of the samples fall, capped at the max. */
export function percentile(t: Timing, q: number): number {
  if (t.n === 0) return 0;
  const want = q * t.n;
  let seen = 0;
  for (let i = 0; i < t.buckets.length; i++) {
    seen += t.buckets[i];
    if (seen >= want) return Math.min(i < BOUNDS_MS.length ? BOUNDS_MS[i] : t.max, t.max);
  }
  return t.max;
}

/** One line for the popup: what the day cost, in the numbers that say whether the caches pay. */
export function workLine(s: Snapshot): string {
  const c = s.counters;
  const judged = c.judged ?? 0;
  if (!judged && !c.video_frames_judged) return "No work recorded yet today.";
  const cached = (c.url_cache_hits ?? 0) + (c.engine_cache_hits ?? 0) + (c.inflight_joins ?? 0);
  const engine = s.timings.engine_ms;
  const parts = [`${judged} judged`, `${cached} from cache`];
  if (engine) parts.push(`model ${percentile(engine, 0.5)} ms median, ${percentile(engine, 0.95)} ms at 95%`);
  if (c.asks) parts.push(`${c.asks} asked the server`);
  if (c.video_frames_judged || c.video_frames_dropped) {
    parts.push(`video ${c.video_frames_judged ?? 0} frames judged, ${c.video_frames_dropped ?? 0} unchanged`);
  }
  return parts.join("; ") + ".";
}
