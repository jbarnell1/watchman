// Runs at document_start on every page. The stylesheet injected with it hides every image until
// the background has judged it; this script asks for the judgement and marks the result. Images are
// judged as they come within a screen of view, nearest first; playing videos are sampled once per
// whole-screen interval and unchanged frames are dropped before any model runs (ADR-023).

import { blobToBase64 } from "./shared/bytes";
import { dhashRgba } from "./shared/dhash";
import { ext } from "./shared/ext";
import type { FrameJudgeRequest, JudgeRequest, JudgeResponse } from "./shared/types";
import { type FrameGrab, VideoSampler } from "./shared/video";

const TIMEOUT_MS = 6000;
const SRC_KEY = "wmSrc";
const NEAR_VIEWPORT = "100%"; // one viewport of margin on every side
const FRAME_SIDE = 384;

type Mark = "pending" | "safe" | "flag" | "cover";
type Judgeable = HTMLImageElement | HTMLVideoElement;

function mark(el: Judgeable, m: Mark): void {
  el.dataset.wm = m;
}

export function shouldJudge(img: HTMLImageElement): boolean {
  const src = img.currentSrc || img.src;
  if (!src) return false;
  return img.dataset[SRC_KEY] !== src;
}

async function askBackground<T extends JudgeResponse>(req: JudgeRequest | FrameJudgeRequest): Promise<T> {
  const timeout = new Promise<T>((resolve) =>
    setTimeout(() => resolve({ decision: "cover", hash: null, score: null, reason: "error" } as T), TIMEOUT_MS),
  );
  const answer = ext.runtime.sendMessage(req) as Promise<T>;
  return Promise.race([answer, timeout]);
}

async function judge(img: HTMLImageElement): Promise<void> {
  const src = img.currentSrc || img.src;
  if (!src) {
    mark(img, "safe");
    return;
  }
  img.dataset[SRC_KEY] = src;
  mark(img, "pending");
  const req: JudgeRequest = {
    type: "judge",
    url: src,
    width: img.naturalWidth,
    height: img.naturalHeight,
    displayWidth: Math.round(img.getBoundingClientRect().width),
    displayHeight: Math.round(img.getBoundingClientRect().height),
    pageUrl: location.href,
    pageTitle: document.title,
  };
  const res = await askBackground(req);
  if (img.dataset[SRC_KEY] !== src) return; // the image changed while we waited
  mark(img, res.decision === "safe" ? "safe" : res.decision === "flag" ? "flag" : "cover");
}

// Viewport gating. Without IntersectionObserver (tests, very old engines) everything is judged at once.

let near: IntersectionObserver | null | undefined;
const onScreen = new WeakSet<Element>();

function nearObserver(): IntersectionObserver | null {
  if (near !== undefined) return near;
  if (typeof IntersectionObserver === "undefined") {
    near = null;
    return near;
  }
  near = new IntersectionObserver(
    (entries) => {
      const due = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => Math.abs(a.boundingClientRect.top) - Math.abs(b.boundingClientRect.top));
      for (const e of due) {
        near!.unobserve(e.target);
        if (e.target instanceof HTMLImageElement) void judge(e.target);
      }
    },
    { rootMargin: NEAR_VIEWPORT },
  );
  return near;
}

/** Judge now if the image is near the viewport, otherwise when it gets there. */
function whenNear(img: HTMLImageElement): void {
  const io = nearObserver();
  if (!io) {
    void judge(img);
    return;
  }
  io.observe(img);
}

function consider(img: HTMLImageElement): void {
  if (!shouldJudge(img)) return;
  if (img.complete && img.naturalWidth > 0) {
    whenNear(img);
    return;
  }
  if (img.complete && !img.currentSrc) {
    mark(img, "safe");
    return;
  }
  img.addEventListener("load", () => whenNear(img), { once: true });
  img.addEventListener("error", () => mark(img, "safe"), { once: true });
}

// Video: sampled while playing and on screen.

let frameCanvas: HTMLCanvasElement | null = null;

async function grabFrame(v: HTMLVideoElement): Promise<FrameGrab | "later" | null> {
  const w = v.videoWidth;
  const h = v.videoHeight;
  if (!w || !h) return "later";
  const scale = Math.min(1, FRAME_SIDE / Math.max(w, h));
  const cw = Math.max(1, Math.round(w * scale));
  const ch = Math.max(1, Math.round(h * scale));
  frameCanvas ??= document.createElement("canvas");
  frameCanvas.width = cw;
  frameCanvas.height = ch;
  const ctx = frameCanvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  let pixels: ImageData;
  try {
    ctx.drawImage(v, 0, 0, cw, ch);
    pixels = ctx.getImageData(0, 0, cw, ch); // throws on a tainted canvas (cross-origin, no CORS)
  } catch {
    return null;
  }
  const hash = dhashRgba(pixels.data, cw, ch);
  const blob = await new Promise<Blob | null>((r) => frameCanvas!.toBlob(r, "image/jpeg", 0.85));
  if (!blob) return null;
  return { hash, jpegBase64: await blobToBase64(blob), width: w, height: h };
}

function videoSource(v: HTMLVideoElement): string {
  return v.currentSrc || v.src || v.querySelector("source")?.src || `${location.href}#video`;
}

async function judgeFrame(v: HTMLVideoElement, frame: FrameGrab): Promise<"safe" | "cover"> {
  const source = videoSource(v);
  v.dataset[SRC_KEY] = source;
  const req: FrameJudgeRequest = {
    type: "judge_frame",
    jpegBase64: frame.jpegBase64,
    hash: frame.hash,
    source,
    width: frame.width,
    height: frame.height,
    displayWidth: Math.round(v.getBoundingClientRect().width),
    displayHeight: Math.round(v.getBoundingClientRect().height),
    pageUrl: location.href,
    pageTitle: document.title,
  };
  const res = await askBackground(req);
  return res.decision === "safe" || res.decision === "flag" ? "safe" : "cover";
}

export const sampler = new VideoSampler({
  grab: grabFrame,
  judge: judgeFrame,
  onScreen: (v) => (near ? onScreen.has(v) : true),
});

let videoNear: IntersectionObserver | null = null;
let droppedReported = 0;

/** Once a minute: how many sampled frames were dropped as unchanged, for the work metrics. */
function reportVideoStats(): void {
  const delta = sampler.dropped - droppedReported;
  if (delta <= 0) return;
  droppedReported = sampler.dropped;
  void ext.runtime.sendMessage({ type: "video_stats", dropped: delta }).catch(() => undefined);
}

function considerVideo(v: HTMLVideoElement): void {
  if (v.dataset.wmVideo) return;
  sampler.track(v);
  if (typeof IntersectionObserver === "undefined") return;
  videoNear ??= new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) onScreen.add(e.target);
      else onScreen.delete(e.target);
    }
  });
  videoNear.observe(v);
}

function scan(root: ParentNode): void {
  root.querySelectorAll("img").forEach(consider);
  root.querySelectorAll("video").forEach(considerVideo);
}

function watch(): void {
  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === "attributes" && m.target instanceof HTMLImageElement) {
        if (shouldJudge(m.target)) {
          delete m.target.dataset.wm;
          consider(m.target);
        }
        continue;
      }
      m.addedNodes.forEach((n) => {
        if (n instanceof HTMLImageElement) consider(n);
        else if (n instanceof HTMLVideoElement) considerVideo(n);
        else if (n instanceof Element) scan(n);
      });
    }
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["src", "srcset"],
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) sampler.stop();
    else if (sampler.size > 0) sampler.start();
  });
}

/** Every image on the page with its natural size, for the popup's page scorer. */
export function listImages(): { url: string; width: number; height: number }[] {
  const seen = new Set<string>();
  const out: { url: string; width: number; height: number }[] = [];
  document.querySelectorAll("img").forEach((img) => {
    const src = img.currentSrc || img.src;
    if (!src || seen.has(src)) return;
    seen.add(src);
    out.push({ url: src, width: img.naturalWidth, height: img.naturalHeight });
  });
  return out;
}

/** A click on a covered image or video: keep it covered, ask the server to look again, show the answer. */
export async function recheck(el: Judgeable): Promise<void> {
  const url = el.dataset[SRC_KEY] || el.currentSrc || el.src;
  if (!url) return;
  el.dataset.wmNote = "checking";
  el.title = "Watchman: checking with the household server";
  let res: { decision?: "safe" | "cover"; reason?: string } = {};
  try {
    let msg: Record<string, unknown> = { type: "recheck", url, pageUrl: location.href };
    if (el instanceof HTMLVideoElement) {
      const frame = await grabFrame(el);
      if (!frame || frame === "later") throw new Error("cannot sample");
      msg = { ...msg, jpegBase64: frame.jpegBase64 };
    }
    res = (await ext.runtime.sendMessage(msg)) as typeof res;
  } catch {
    res = {};
  }
  if (res.decision === "safe") {
    revise(url, "safe");
    mark(el, "safe");
    el.title = "";
    delete el.dataset.wmNote;
    return;
  }
  el.dataset.wmNote = "kept";
  el.title = res.reason === "known_bad" || res.reason === "explicit"
    ? "Watchman: kept covered. The household server agrees."
    : "Watchman: kept covered. The server could not be reached.";
}

document.addEventListener(
  "click",
  (e) => {
    const target = e.target as HTMLElement | null;
    const judgeable = target instanceof HTMLImageElement || target instanceof HTMLVideoElement;
    if (!judgeable || target.dataset.wm !== "cover" || target.dataset.wmNote === "checking") return;
    e.preventDefault();
    e.stopPropagation();
    void recheck(target);
  },
  true,
);

/** The server's answer for an image or video that was covered while unsure. */
export function revise(url: string, decision: "safe" | "cover"): number {
  let n = 0;
  document.querySelectorAll<Judgeable>("img, video").forEach((el) => {
    if (el.dataset[SRC_KEY] === url) {
      mark(el, decision);
      n++;
    }
  });
  return n;
}

ext.runtime.onMessage.addListener((raw: unknown, _sender, sendResponse) => {
  const msg = raw as { type?: string; url?: string; decision?: "safe" | "cover" };
  if (msg?.type === "list_images") { sendResponse(listImages()); return false; }
  if (msg?.type === "revise" && msg.url && msg.decision) { sendResponse(revise(msg.url, msg.decision)); return false; }
  return false;
});

async function main(): Promise<void> {
  let active = false;
  let report = false;
  try {
    const r = (await ext.runtime.sendMessage({ type: "active" })) as { active: boolean; coverOnDevice?: boolean; videoSampleS?: number };
    active = Boolean(r?.active);
    report = r?.coverOnDevice === false;
    if (r?.videoSampleS) sampler.setIntervalMs(r.videoSampleS * 1000);
  } catch {
    active = false;
  }
  if (!active) {
    document.documentElement.classList.add("wm-off");
    return;
  }
  // Report only: images are not held back; they are still judged and reported.
  if (report) document.documentElement.classList.add("wm-report");
  scan(document);
  watch();
  setInterval(reportVideoStats, 60_000);
}

if (typeof document !== "undefined" && !(globalThis as { __WATCHMAN_TEST__?: boolean }).__WATCHMAN_TEST__) {
  void main();
}

export { consider, considerVideo, judge, scan, watch };
