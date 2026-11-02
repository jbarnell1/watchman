// The background: holds the pairing and policy, judges images for content scripts, queues events,
// heartbeats every five minutes. Scoring itself happens on the engine page (engine/page.ts).

import { Api, ApiError } from "./shared/api";
import { base64ToBytes } from "./shared/bytes";
import { DecisionCache, decide, Inflight, isKnownBad, isTiny, Lru, RateLimiter, settle, shouldAsk } from "./shared/decide";
import { ext, nowIso, platformName, StorageKeyValue, uuid } from "./shared/ext";
import { EventQueue } from "./shared/queue";
import { count, emptySnapshot, isEmpty, merge, type Snapshot, time, workLine } from "./shared/metrics";
import type {
  Decision,
  FrameJudgeRequest,
  JudgeRequest,
  JudgeResponse,
  ModelEntry,
  PageImage,
  PageScore,
  Pairing,
  Policy,
  ScoreFailure,
  ScoreResult,
  Thresholds,
  WatchmanEvent,
} from "./shared/types";

export const VERSION = "0.1.0";
const HEARTBEAT_MINUTES = 5;
const FLUSH_MINUTES = 0.5;
const UPLOAD_SIDE = 512;
const MODEL_ROLES = ["crop_classifier_int8", "crop_classifier"];

const storage = new StorageKeyValue();
const queue = new EventQueue(storage);
const cache = new DecisionCache();
const pendingImages = new Lru<string>(300); // event id -> JPEG base64, until the server asks
const inflight = new Inflight<JudgeResponse>(); // one fetch and one inference per URL at a time (ADR-023)
const inflightTabs = new Map<string, Set<number>>(); // tabs waiting on an in-flight URL, for the revise
const videoEvents = new RateLimiter(60_000); // one report per video source per minute

// ADR-025: tallies in memory, folded into storage every flush (the service worker can be stopped at
// any time), sent with the heartbeat, and kept per day for the popup.
let work: Snapshot = emptySnapshot();
let workSince = Date.now();
let debug = false;

function tally(name: string, n = 1): void {
  count(work, name, n);
}

function timing(name: string, ms: number): void {
  time(work, name, ms);
}

/** Move the in-memory window into storage: one copy for the next heartbeat, one for today. */
async function foldWork(): Promise<void> {
  if (isEmpty(work)) return;
  const w = { ...work, window_s: Math.round((Date.now() - workSince) / 1000) };
  work = emptySnapshot();
  workSince = Date.now();
  const pending = ((await storage.get("work_pending")) as Snapshot | undefined) ?? emptySnapshot();
  await storage.set("work_pending", merge(pending, w));
  const today = new Date().toISOString().slice(0, 10);
  const day = (await storage.get("work_day")) as { day: string; work: Snapshot } | undefined;
  await storage.set("work_day", { day: today, work: merge(day?.day === today ? day.work : emptySnapshot(), w) });
}

interface Counters {
  day: string;
  scored: number;
  covered: number;
  flagged: number;
}

async function pairing(): Promise<Pairing | null> {
  return ((await storage.get("pairing")) as Pairing | undefined) ?? null;
}

async function policy(): Promise<Policy | null> {
  return ((await storage.get("policy")) as Policy | undefined) ?? null;
}

async function api(): Promise<Api | null> {
  const p = await pairing();
  return p ? new Api(fetch, p.server, p.token) : null;
}

async function counters(): Promise<Counters> {
  const today = new Date().toISOString().slice(0, 10);
  const c = (await storage.get("counters")) as Counters | undefined;
  if (!c || c.day !== today) return { day: today, scored: 0, covered: 0, flagged: 0 };
  return c;
}

// Judgements run concurrently; serialise the read-modify-write so no increment is lost.
let counterChain: Promise<void> = Promise.resolve();

function bump(field: "scored" | "covered" | "flagged"): Promise<void> {
  counterChain = counterChain.then(async () => {
    const c = await counters();
    c[field] += 1;
    await storage.set("counters", c);
  });
  return counterChain;
}

// Engine page: Chrome offscreen document, or an iframe in Firefox's background page.

let engineReady: Promise<void> | null = null;

async function ensureEnginePage(): Promise<void> {
  const offscreen = (chrome as unknown as { offscreen?: typeof chrome.offscreen }).offscreen;
  if (offscreen) {
    const has = await offscreen.hasDocument?.();
    if (!has) {
      await offscreen.createDocument({
        url: "engine.html",
        reasons: ["WORKERS" as chrome.offscreen.Reason],
        justification: "Scores images with an on-device model before they are shown.",
      });
    }
    return;
  }
  if (typeof document !== "undefined" && !document.getElementById("wm-engine")) {
    const frame = document.createElement("iframe");
    frame.id = "wm-engine";
    frame.src = ext.runtime.getURL("engine.html");
    frame.style.display = "none";
    document.body.appendChild(frame);
    await new Promise((r) => frame.addEventListener("load", r, { once: true }));
  }
}

async function engineCall<T>(msg: Record<string, unknown>): Promise<T> {
  return (await ext.runtime.sendMessage({ target: "engine", ...msg })) as T;
}

async function pickModel(a: Api): Promise<ModelEntry> {
  const manifest = await a.manifest();
  for (const role of MODEL_ROLES) {
    const entry = manifest.models.find((m) => m.role === role);
    if (entry) return entry;
  }
  throw new Error("the server's manifest has no crop classifier");
}

async function ensureEngine(): Promise<void> {
  if (engineReady) return engineReady;
  engineReady = (async () => {
    const p = await pairing();
    const a = await api();
    if (!p || !a) throw new Error("not paired");
    await ensureEnginePage();
    const ping = await engineCall<{ ok: boolean; ready: boolean }>({ type: "ping" });
    if (ping?.ready) return;
    const entry = await pickModel(a);
    const r = await engineCall<{ ok: boolean; error?: string }>({ type: "load", server: p.server, token: p.token, entry });
    if (!r?.ok) throw new Error(r?.error ?? "engine failed to load");
  })();
  engineReady.catch(() => {
    engineReady = null;
  });
  return engineReady;
}

// Judging

/** Ask the server's bigger model about a crop the device was unsure of, then tell the tabs. */
async function askServer(req: JudgeRequest | FrameJudgeRequest, tabId: number | undefined, result: ScoreResult, pol: Policy): Promise<void> {
  const a = await api();
  if (!a || !result.jpegBase64) return;
  const url = sourceOf(req);
  let verdict: "safe" | "explicit";
  const t0 = performance.now();
  tally("asks");
  try {
    verdict = (await a.judge(base64ToBytes(result.jpegBase64).buffer as ArrayBuffer)).verdict;
  } catch (err) {
    tally("ask_failed");
    console.warn("watchman: ask failed, staying covered", err);
    return;
  }
  timing("ask_ms", performance.now() - t0);
  tally(verdict === "explicit" ? "ask_explicit" : "ask_safe");
  const decision: Decision = verdict === "explicit" ? "cover" : "safe";
  cache.remember(url, { decision, hash: result.hash, score: result.score });
  if (decision === "cover") await record(req, result, settle("cover", pol.cover_on_device !== false));
  const tabs = new Set(inflightTabs.get(url) ?? []);
  inflightTabs.delete(url);
  if (tabId !== undefined) tabs.add(tabId);
  for (const id of tabs) {
    try {
      await ext.tabs.sendMessage(id, { type: "revise", url, decision });
    } catch {
      // the tab is gone
    }
  }
}

function sourceOf(req: JudgeRequest | FrameJudgeRequest): string {
  return req.type === "judge" ? req.url : req.source;
}

async function record(req: JudgeRequest | FrameJudgeRequest, result: ScoreResult, decision: Decision): Promise<void> {
  const video = req.type === "judge_frame";
  if (video && !videoEvents.allow(req.source)) return;
  const ev: WatchmanEvent = {
    id: uuid(),
    type: decision === "cover" ? "image_covered" : "image_flagged",
    occurred_at: nowIso(),
    app: platformName(),
    url: req.pageUrl,
    title: req.pageTitle || null,
    action: decision === "cover" ? "covered" : "flagged",
    scores: { classifier: Math.round(result.score * 1000) / 1000 },
    region: { w: req.displayWidth, h: req.displayHeight, screen_w: 0, screen_h: 0 },
    hash: result.hash,
    has_image: result.jpegBase64 !== null,
    detail: { image_url: sourceOf(req), natural_w: result.width, natural_h: result.height, video },
  };
  if (result.jpegBase64) pendingImages.set(ev.id, result.jpegBase64);
  await queue.push(ev);
  await bump(decision === "cover" ? "covered" : "flagged");
}

export async function judge(req: JudgeRequest | FrameJudgeRequest, tabId?: number): Promise<JudgeResponse> {
  const t0 = performance.now();
  const res = await judgeInner(req, tabId);
  const ms = performance.now() - t0;
  if (res.reason !== "unpaired" && res.reason !== "paused") {
    timing(req.type === "judge" ? "judge_ms" : "video_judge_ms", ms);
    tally(req.type === "judge" ? "judged" : "video_frames_judged");
    if (res.reason === "tiny") tally("tiny");
    if (res.reason === "cached") tally("url_cache_hits");
    if (res.reason === "error") tally("errors");
    if (res.decision === "cover") tally("covered_on_device");
  }
  if (debug) console.debug("watchman judge", Math.round(ms), "ms", res.reason, res.score, req.type === "judge" ? req.url : req.source);
  return res;
}

async function judgeInner(req: JudgeRequest | FrameJudgeRequest, tabId?: number): Promise<JudgeResponse> {
  const p = await pairing();
  const pol = await policy();
  if (!p || !pol) return { decision: "safe", hash: null, score: null, reason: "unpaired" };
  if (pol.paused_until && new Date(pol.paused_until) > new Date()) {
    return { decision: "safe", hash: null, score: null, reason: "paused" };
  }
  if (isTiny(req.width, req.height)) return { decision: "safe", hash: null, score: null, reason: "tiny" };
  const url = sourceOf(req);
  const hit = req.type === "judge" ? cache.lookup(url) : cache.lookup(url, req.hash) ?? cache.lookup(req.hash);
  if (hit) return { decision: hit.decision, hash: hit.hash, score: hit.score, reason: "cached" };
  if (req.type === "judge_frame") return judgeOnce(req, tabId, pol);
  // Fifty copies of one avatar are one fetch and one inference; every tab gets the revise.
  if (inflight.has(url)) tally("inflight_joins");
  if (inflight.has(url) && tabId !== undefined) {
    const tabs = inflightTabs.get(url) ?? new Set<number>();
    tabs.add(tabId);
    inflightTabs.set(url, tabs);
  }
  return inflight.run(url, () => judgeOnce(req, tabId, pol));
}

async function judgeOnce(req: JudgeRequest | FrameJudgeRequest, tabId: number | undefined, pol: Policy): Promise<JudgeResponse> {
  try {
    await ensureEngine();
  } catch (err) {
    console.warn("watchman: engine unavailable", err);
    return { decision: "cover", hash: null, score: null, reason: "error" };
  }
  const result = await engineCall<ScoreResult | ScoreFailure>(
    req.type === "judge"
      ? { type: "score", url: req.url, maxSide: UPLOAD_SIDE, wantImage: true }
      : { type: "score_bytes", jpegBase64: req.jpegBase64 },
  );
  if (!result?.ok) {
    return { decision: "cover", hash: null, score: null, reason: "error" };
  }
  if (result.ms !== undefined) timing("engine_ms", result.ms);
  if (result.cached) tally("engine_cache_hits");
  const url = sourceOf(req);
  await bump("scored");
  let decision = decide(result.score, pol.thresholds);
  let reason: JudgeResponse["reason"] = "scored";
  if (isKnownBad(result.hash, pol.known_bad_hashes)) {
    decision = "cover";
    reason = "known_bad";
  }
  const nearby = cache.lookup(url, result.hash);
  if (nearby && nearby.decision === "cover") decision = "cover";
  const coverOnDevice = pol.cover_on_device !== false;
  decision = settle(decision, coverOnDevice);

  // Unsure: cover now, let the server's bigger model decide, revise the tab when it answers.
  if (coverOnDevice && decision !== "cover" && reason === "scored" && shouldAsk(result.score, pol.thresholds, pol.ask_server !== false)) {
    void askServer(req, tabId, result, pol);
    return { decision: "cover", hash: result.hash, score: result.score, reason: "asking" };
  }

  cache.remember(url, { decision, hash: result.hash, score: result.score });
  if (decision !== "safe") await record(req, result, decision);
  return { decision, hash: result.hash, score: result.score, reason };
}

/** A second look at a covered image, on request from the page: the server's bigger model decides. */
async function recheck(url: string, pageUrl: string, jpegBase64?: string): Promise<{ decision: Decision; reason: string }> {
  const pol = await policy();
  const a = await api();
  if (!pol || !a) return { decision: "cover", reason: "unpaired" };
  try {
    await ensureEngine();
  } catch {
    return { decision: "cover", reason: "error" };
  }
  const r = await engineCall<ScoreResult | ScoreFailure>(
    jpegBase64 ? { type: "score_bytes", jpegBase64 } : { type: "score", url, maxSide: UPLOAD_SIDE, wantImage: true },
  );
  if (!r?.ok || !r.jpegBase64) return { decision: "cover", reason: "error" };
  if (isKnownBad(r.hash, pol.known_bad_hashes)) return { decision: "cover", reason: "known_bad" };
  try {
    const verdict = (await a.judge(base64ToBytes(r.jpegBase64).buffer as ArrayBuffer)).verdict;
    const decision: Decision = verdict === "explicit" ? "cover" : "safe";
    cache.remember(url, { decision, hash: r.hash, score: r.score });
    await bump("scored");
    return { decision, reason: verdict === "explicit" ? "explicit" : "scored" };
  } catch {
    return { decision: "cover", reason: "error" };
  }
  void pageUrl;
}

/** The popup's diagnostic: every image on the active tab with its score. Nothing is stored or sent. */
async function scorePage(): Promise<{ ok: boolean; error?: string; images?: PageScore[]; thresholds?: Thresholds }> {
  const pol = await policy();
  if (!pol) return { ok: false, error: "not paired" };
  const [tab] = await ext.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return { ok: false, error: "no active tab" };
  let images: PageImage[];
  try {
    images = (await ext.tabs.sendMessage(tab.id, { type: "list_images" })) as PageImage[];
  } catch {
    return { ok: false, error: "this page has no Watchman content script (reload it)" };
  }
  try {
    await ensureEngine();
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
  const out: PageScore[] = [];
  for (const img of images.slice(0, 300)) {
    if (isTiny(img.width, img.height)) { out.push({ ...img, hash: null, score: null, decision: "tiny" }); continue; }
    const r = await engineCall<ScoreResult | ScoreFailure>({ type: "score", url: img.url, maxSide: 64, wantImage: false });
    if (!r?.ok) { out.push({ ...img, hash: null, score: null, decision: "error" }); continue; }
    let decision: PageScore["decision"] = decide(r.score, pol.thresholds);
    if (isKnownBad(r.hash, pol.known_bad_hashes)) decision = "cover";
    out.push({ url: img.url, width: r.width, height: r.height, hash: r.hash, score: r.score, decision });
  }
  out.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  return { ok: true, images: out, thresholds: pol.thresholds };
}

// Server traffic

async function flush(): Promise<void> {
  await foldWork();
  const a = await api();
  if (!a) return;
  // Settings reach the extension within a flush interval instead of a heartbeat (ADR-031).
  try {
    const v = await a.policyVersion();
    const current = await policy();
    if (!current || current.version !== v.policy_version || current.paused_until !== v.paused_until) {
      await storage.set("policy", await a.policy());
    }
  } catch {
    // the heartbeat will try again
  }
  try {
    const wanted = await queue.flush((batch) => a.events(batch));
    for (const id of wanted) {
      const b64 = pendingImages.get(id);
      if (!b64) continue;
      await a.uploadImage(id, base64ToBytes(b64).buffer as ArrayBuffer);
    }
  } catch (err) {
    console.warn("watchman: flush failed", err);
  }
}

async function heartbeat(): Promise<void> {
  const a = await api();
  if (!a) return;
  await foldWork();
  const pending = ((await storage.get("work_pending")) as Snapshot | undefined) ?? emptySnapshot();
  try {
    const r = await a.heartbeat({
      sent_at: nowIso(),
      app_version: VERSION,
      booted_at: null,
      safe_mode: false,
      battery_pct: null,
      queued_events: await queue.size(),
      state: {
        protection: true,
        accessibility: null,
        capture: "not_applicable",
        overlay: null,
        device_admin: null,
        extension: true,
        vpn: null,
      },
      metrics: isEmpty(pending) ? null : pending,
    });
    await storage.set("work_pending", emptySnapshot());
    const current = await policy();
    if (!current || current.version !== r.policy_version || current.paused_until !== r.paused_until) {
      await storage.set("policy", await a.policy());
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      await storage.remove("pairing");
      await storage.remove("policy");
    }
    console.warn("watchman: heartbeat failed", err);
  }
}

async function pair(server: string, code: string, name: string): Promise<Pairing> {
  const p = await Api.pair(fetch, server, code, {
    name: name || `${platformName()} extension`,
    platform: platformName(),
    kind: "extension",
    app_version: VERSION,
  });
  await storage.set("pairing", p);
  const a = new Api(fetch, p.server, p.token);
  await storage.set("policy", await a.policy());
  engineReady = null;
  await heartbeat();
  return p;
}

async function todaysWork(): Promise<Snapshot> {
  await foldWork();
  const today = new Date().toISOString().slice(0, 10);
  const day = (await storage.get("work_day")) as { day: string; work: Snapshot } | undefined;
  return day?.day === today ? day.work : emptySnapshot();
}

async function status(): Promise<Record<string, unknown>> {
  const p = await pairing();
  const pol = await policy();
  const c = await counters();
  return {
    paired: p !== null,
    server: p?.server ?? null,
    serverName: p?.server_name ?? null,
    pausedUntil: pol?.paused_until ?? null,
    policyVersion: pol?.version ?? null,
    queued: await queue.size(),
    counters: c,
    version: VERSION,
    work: workLine(await todaysWork()),
    coverOnDevice: pol?.cover_on_device !== false,
    askServer: pol?.ask_server !== false,
  };
}

// Wiring

ext.runtime.onMessage.addListener((raw: unknown, sender, sendResponse) => {
  const msg = raw as { type?: string; target?: string } & Record<string, unknown>;
  if (!msg || msg.target === "engine") return false;
  (async () => {
    switch (msg.type) {
      case "judge":
        return judge(msg as unknown as JudgeRequest, sender.tab?.id);
      case "active": {
        const p = await pairing();
        const pol = await policy();
        return { active: p !== null, coverOnDevice: pol?.cover_on_device !== false, videoSampleS: pol?.whole_screen_interval_s ?? 5 };
      }
      case "status":
        return status();
      case "pair":
        try {
          return { ok: true, pairing: await pair(String(msg.server), String(msg.code), String(msg.name ?? "")) };
        } catch (err) {
          return { ok: false, error: err instanceof Error ? err.message : String(err) };
        }
      case "unlock": {
        const a = await api();
        if (!a) return { ok: false, error: "not paired" };
        try {
          const r = await a.unlock(String(msg.code));
          const pol = await policy();
          if (pol) await storage.set("policy", { ...pol, paused_until: r.paused_until });
          return { ok: true, paused_until: r.paused_until };
        } catch (err) {
          return { ok: false, error: err instanceof Error ? err.message : String(err) };
        }
      }
      case "request_unlock": {
        const a = await api();
        if (!a) return { ok: false, error: "not paired" };
        try {
          await a.requestUnlock(Number(msg.minutes), String(msg.reason ?? ""));
          return { ok: true };
        } catch (err) {
          return { ok: false, error: err instanceof Error ? err.message : String(err) };
        }
      }
      case "flush":
        await flush();
        return { ok: true };
      case "score_page":
        return scorePage();
      case "video_stats":
        tally("video_frames_dropped", Number(msg.dropped ?? 0));
        return { ok: true };
      case "judge_frame":
        return judge(msg as unknown as FrameJudgeRequest, sender.tab?.id);
      case "recheck":
        return recheck(String(msg.url), String(msg.pageUrl ?? ""), msg.jpegBase64 ? String(msg.jpegBase64) : undefined);
      default:
        return { ok: false, error: "unknown message" };
    }
  })().then(sendResponse);
  return true;
});

ext.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "heartbeat") void heartbeat();
  if (alarm.name === "flush") void flush();
});

async function start(): Promise<void> {
  debug = Boolean(await storage.get("watchmanDebug"));
  await ext.alarms.create("heartbeat", { periodInMinutes: HEARTBEAT_MINUTES });
  await ext.alarms.create("flush", { periodInMinutes: FLUSH_MINUTES });
  await heartbeat();
}

// For the end-to-end check and for debugging from the service worker console.
(globalThis as unknown as { watchmanDiagnose: () => Promise<unknown> }).watchmanDiagnose = () =>
  ensureEngine()
    .then(() => ({ ok: true }))
    .catch((err: unknown) => ({ ok: false, error: err instanceof Error ? `${err.message}
${err.stack ?? ""}` : String(err) }));

ext.runtime.onInstalled.addListener(() => void start());
ext.runtime.onStartup.addListener(() => void start());
