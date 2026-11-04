import { describe, expect, it } from "vitest";

import { addJudged, addScreenSample, delivered, freshLiveness, livenessBody, MAX_SAMPLE_GAP_MS } from "../src/shared/liveness";

describe("judge liveness", () => {
  it("counts screen time between samples only while the user is active, capped per gap", () => {
    let s = freshLiveness(0);
    s = addScreenSample(s, 0, true); // the first sample only starts the clock
    s = addScreenSample(s, 30_000, true);
    s = addScreenSample(s, 60_000, false);
    s = addScreenSample(s, 60_000 + 10 * MAX_SAMPLE_GAP_MS, true); // the worker slept: one gap at most
    expect(s.screen_on_ms).toBe(30_000 + MAX_SAMPLE_GAP_MS);
  });

  it("builds the heartbeat field and keeps what arrived while it was in flight", () => {
    let s = addJudged(freshLiveness(0), 41, "2026-11-03T10:00:00Z");
    s = { ...s, screen_on_ms: 240_000 };
    const body = livenessBody(s, 300_000);
    expect(body).toEqual({ window_s: 300, screen_on_s: 240, judged: 41, last_judged_at: "2026-11-03T10:00:00Z" });
    s = addJudged(s, 2, "2026-11-03T10:05:01Z"); // folded while the request was out
    const next = delivered(s, body, 300_000);
    expect(next.judged).toBe(2);
    expect(next.screen_on_ms).toBe(0);
    expect(next.last_judged_at).toBe("2026-11-03T10:05:01Z");
    expect(livenessBody(next, 360_000).window_s).toBe(60);
  });

  it("reports null before anything was ever judged", () => {
    expect(livenessBody(freshLiveness(0), 1000).last_judged_at).toBeNull();
  });
});
