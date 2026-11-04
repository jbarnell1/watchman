import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JudgeRequest, Policy } from "../src/shared/types";
import { fakeChrome, resetStore } from "./setup";

const bg = await import("../src/background");

const SERVER = "https://watchman.test";
const PAIRING = { server: SERVER, token: "old", device_id: "d", person_id: "p", server_name: "Example household" };
const POLICY: Policy = { ...bg.DEFAULT_POLICY, version: 7 };

type Handler = (url: string, init?: RequestInit) => { status: number; body: unknown };

function serve(handler: Handler): { bodies: Record<string, unknown>[] } {
  const seen = { bodies: [] as Record<string, unknown>[] };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/api/v1/heartbeat") && init?.body) seen.bodies.push(JSON.parse(String(init.body)));
    const r = handler(url, init);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { "Content-Type": "application/json" } });
  }));
  return seen;
}

const healthy: Handler = (url) => {
  if (url.endsWith("/api/v1/heartbeat")) return { status: 200, body: { policy_version: 7, paused_until: null } };
  if (url.endsWith("/api/v1/policy")) return { status: 200, body: POLICY };
  if (url.endsWith("/api/v1/pair")) return { status: 200, body: { device_id: "d2", token: "new", person_id: "p", server_name: "Example household" } };
  if (url.endsWith("/api/v1/judge")) return { status: 200, body: { score: 0.9, verdict: "explicit" } };
  return { status: 404, body: { error: { code: "not_found", message: "no" } } };
};

const rejecting: Handler = () => ({ status: 401, body: { error: { code: "unauthorized", message: "unknown token" } } });

/** The engine page, faked: every image scores `score`. */
function engineScores(score: number): void {
  (fakeChrome.runtime.sendMessage as unknown as { mockImplementation: (f: (m: { type: string }) => unknown) => void })
    .mockImplementation(async (m) => {
      if (m.type === "ping") return { ok: true, ready: true };
      if (m.type === "score" || m.type === "score_bytes") {
        // A different picture every time, so no earlier decision matches it by hash.
        const hash = Math.floor(Math.random() * 2 ** 32).toString(16).padStart(8, "0").repeat(2);
        return { ok: true, hash, score, width: 400, height: 300, jpegBase64: null };
      }
      return {};
    });
}

let n = 0;
function request(): JudgeRequest {
  n += 1;
  return { type: "judge", url: `https://x/${n}.jpg`, width: 400, height: 300, displayWidth: 400, displayHeight: 300, pageUrl: "https://x/", pageTitle: "x" };
}

async function stored(key: string): Promise<unknown> {
  return ((await chrome.storage.local.get(key)) as Record<string, unknown>)[key];
}

describe("background", () => {
  beforeEach(() => {
    resetStore();
    fakeChrome.runtime.sendMessage.mockReset();
    if (typeof navigator === "undefined") vi.stubGlobal("navigator", { userAgent: "Chrome/130" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("a 401 on the heartbeat keeps the pairing, the policy and protection, and says so", async () => {
    await chrome.storage.local.set({ pairing: PAIRING, policy: POLICY });
    serve(rejecting);
    await bg.heartbeat();
    expect(await stored("pairing")).toEqual(PAIRING);
    expect(await stored("policy")).toEqual(POLICY);
    const s = await bg.status();
    expect(s.paired).toBe(true);
    expect(s.rejected).toBe(true);

    engineScores(0.99);
    expect((await bg.judge(request())).decision).toBe("cover");

    // The next heartbeat still goes out on schedule.
    await bg.heartbeat();
    expect((fetch as unknown as { mock: { calls: [string][] } }).mock.calls.filter((c) => c[0].endsWith("/heartbeat"))).toHaveLength(2);

    // Pairing again replaces the token and clears the status.
    serve(healthy);
    await bg.pair(SERVER, "K7Q-4MZ-P2D", "test");
    expect((await stored("pairing")) as { token: string }).toMatchObject({ token: "new" });
    expect((await bg.status()).rejected).toBe(false);
  });

  it("a pairing without a policy judges with the default thresholds instead of passing", async () => {
    await chrome.storage.local.set({ pairing: PAIRING });
    serve(healthy);
    engineScores(0.99);
    expect(await bg.judge(request())).toMatchObject({ decision: "cover", reason: "scored" });
    engineScores(0.9);
    expect(await bg.judge(request())).toMatchObject({ decision: "cover", reason: "asking" });
    engineScores(0.1);
    expect(await bg.judge(request())).toMatchObject({ decision: "safe", reason: "scored" });
  });

  it("only a browser that was never paired passes images through", async () => {
    engineScores(0.99);
    expect(await bg.judge(request())).toMatchObject({ decision: "safe", reason: "unpaired" });
  });

  it("the heartbeat reports judge liveness and carries it over a failed heartbeat", async () => {
    await chrome.storage.local.set({ pairing: PAIRING, policy: POLICY });
    const seen = serve(healthy);
    await bg.heartbeat(); // a clean window
    engineScores(0.1);
    await bg.judge(request());
    await bg.judge(request());
    await bg.heartbeat();
    const judge = seen.bodies.at(-1)!.judge as { window_s: number; screen_on_s: number; judged: number; last_judged_at: string | null };
    expect(judge.judged).toBe(2);
    expect(judge.last_judged_at).toMatch(/^\d{4}-\d\d-\d\dT/);
    expect(typeof judge.window_s).toBe("number");
    expect(judge.screen_on_s).toBeLessThanOrEqual(judge.window_s);

    await bg.judge(request());
    serve(() => ({ status: 503, body: { error: { code: "down", message: "down" } } }));
    await bg.heartbeat();
    await bg.judge(request());
    const again = serve(healthy);
    await bg.heartbeat();
    expect((again.bodies[0].judge as { judged: number }).judged).toBe(2);
  });

  it("a tamper report from a page is queued as a tamper event", async () => {
    await chrome.storage.local.set({ pairing: PAIRING, policy: POLICY });
    const calls = fakeChrome.runtime.onMessage.addListener.mock.calls as unknown as [(m: unknown, s: unknown, r: (x: unknown) => void) => boolean][];
    const listener = calls[0][0];
    const answer = await new Promise((resolve) => listener(
      { type: "tamper", reason: "page_unblur", count: 3, url: "https://x/page" },
      { tab: { id: 1 } },
      resolve,
    ));
    expect(answer).toEqual({ ok: true });
    const queue = (await stored("event_queue")) as { type: string; action: string; url: string; detail: unknown }[];
    expect(queue.at(-1)).toMatchObject({ type: "tamper", action: "none", url: "https://x/page", detail: { reason: "page_unblur", count: 3 } });
  });
});
