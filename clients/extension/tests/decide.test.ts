import { describe, expect, it } from "vitest";

import { DecisionCache, decide, Inflight, isKnownBad, isTiny, Lru, RateLimiter, settle, shouldAsk } from "../src/shared/decide";

const t = { cover: 0.8, flag: 0.6, detector_cover: 0.55 };

describe("decide", () => {
  it("maps scores to decisions at the policy thresholds", () => {
    expect(decide(0.1, t)).toBe("safe");
    expect(decide(0.6, t)).toBe("flag");
    expect(decide(0.79, t)).toBe("flag");
    expect(decide(0.8, t)).toBe("cover");
  });

  it("the ask band sits between ask and cover", () => {
    const ta = { ...t, ask: 0.6 };
    expect(shouldAsk(0.5, ta)).toBe(false);
    expect(shouldAsk(0.6, ta)).toBe(true);
    expect(shouldAsk(0.79, ta)).toBe(true);
    expect(shouldAsk(0.8, ta)).toBe(false);
    expect(shouldAsk(0.7, t)).toBe(false); // no ask line: never ask
    expect(shouldAsk(0.7, ta, false)).toBe(false); // ask_server off: the device decides alone
  });

  it("report only turns a cover into a flag and leaves the rest alone", () => {
    expect(settle("cover", false)).toBe("flag");
    expect(settle("cover", true)).toBe("cover");
    expect(settle("safe", false)).toBe("safe");
    expect(settle("flag", false)).toBe("flag");
  });

  it("icons are not pictures", () => {
    expect(isTiny(16, 16)).toBe(true);
    expect(isTiny(48, 48)).toBe(false);
    expect(isTiny(300, 12)).toBe(true);
  });

  it("known-bad hashes match within the tolerance", () => {
    expect(isKnownBad("9f3a0c1e5b7d2a46", ["9f3a0c1e5b7d2a46"])).toBe(true);
    expect(isKnownBad("9f3a0c1e5b7d2a47", ["9f3a0c1e5b7d2a46"])).toBe(true);
    expect(isKnownBad("0000000000000000", ["9f3a0c1e5b7d2a46"])).toBe(false);
  });

  it("the cache forgets its oldest entries", () => {
    const lru = new Lru<number>(2);
    lru.set("a", 1);
    lru.set("b", 2);
    lru.get("a");
    lru.set("c", 3);
    expect(lru.get("b")).toBeUndefined();
    expect(lru.get("a")).toBe(1);
    expect(lru.size).toBe(2);
  });

  it("in-flight work is shared by key and forgotten when done", async () => {
    const inflight = new Inflight<number>();
    let calls = 0;
    let release: (v: number) => void = () => undefined;
    const work = () => { calls++; return new Promise<number>((r) => { release = r; }); };
    const a = inflight.run("u", work);
    const b = inflight.run("u", work);
    expect(calls).toBe(1);
    expect(inflight.has("u")).toBe(true);
    release(7);
    expect(await a).toBe(7);
    expect(await b).toBe(7);
    expect(inflight.has("u")).toBe(false);
    void inflight.run("u", work);
    expect(calls).toBe(2);
  });

  it("the rate limiter allows one event per key per window", () => {
    let now = 1000;
    const limiter = new RateLimiter(60_000, () => now);
    expect(limiter.allow("v")).toBe(true);
    expect(limiter.allow("v")).toBe(false);
    expect(limiter.allow("w")).toBe(true);
    now += 60_001;
    expect(limiter.allow("v")).toBe(true);
  });

  it("decisions are found by hash or url", () => {
    const cache = new DecisionCache();
    cache.remember("https://x/a.jpg", { decision: "cover", hash: "aa", score: 0.9 });
    expect(cache.lookup("https://x/a.jpg")?.decision).toBe("cover");
    expect(cache.lookup("https://y/other.jpg", "aa")?.decision).toBe("cover");
    expect(cache.lookup("https://y/other.jpg")).toBeUndefined();
  });
});
