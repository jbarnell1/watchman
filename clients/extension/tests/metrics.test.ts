import { describe, expect, it } from "vitest";

import { count, emptySnapshot, merge, percentile, time, workLine } from "../src/shared/metrics";

describe("work metrics", () => {
  it("buckets, merges and reads percentiles back", () => {
    const a = emptySnapshot();
    for (const ms of [3, 12, 14, 15, 30, 300]) time(a, "engine_ms", ms);
    count(a, "judged", 6);
    expect(a.timings.engine_ms.buckets).toEqual([0, 0, 1, 0, 3, 1, 0, 0, 0, 1, 0, 0]);
    expect(percentile(a.timings.engine_ms, 0.5)).toBe(16);
    expect(percentile(a.timings.engine_ms, 1)).toBe(300);
    const b = emptySnapshot();
    count(b, "judged", 4);
    time(b, "engine_ms", 5000);
    const m = merge(a, b);
    expect(m.counters.judged).toBe(10);
    expect(m.timings.engine_ms.n).toBe(7);
    expect(m.timings.engine_ms.buckets[11]).toBe(1);
    expect(a.timings.engine_ms.n).toBe(6); // merge does not touch its inputs
  });

  it("the popup line says what the caches saved", () => {
    const s = emptySnapshot();
    count(s, "judged", 40);
    count(s, "url_cache_hits", 25);
    count(s, "engine_cache_hits", 5);
    time(s, "engine_ms", 20);
    count(s, "video_frames_dropped", 9);
    expect(workLine(s)).toBe("40 judged; 30 from cache; model 20 ms median, 20 ms at 95%; video 0 frames judged, 9 unchanged.");
    expect(workLine(emptySnapshot())).toBe("No work recorded yet today.");
  });
});
