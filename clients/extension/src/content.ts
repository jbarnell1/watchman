// Runs at document_start on every page. The stylesheet injected with it hides every image this
// script has not reached yet; from then on the script holds each decision in its own isolated
// world (a WeakMap keyed by element) and applies it as inline !important styles, which beat any
// stylesheet the page has. When the page changes or removes those styles they are put back, and a
// page that keeps doing it is reported as tampering. Nothing the page can write (attributes,
// classes, the style attribute) is read as the truth: data-wm only mirrors the decision, for the
// stylesheet's first moment and for debugging.
//
// Images are judged as they come within a screen of view, nearest first, and judged again when
// their source changes (src, srcset, a <picture> source). Images in shadow roots and CSS
// background images are judged too. Playing videos are sampled once per whole-screen interval and
// unchanged frames are dropped before any model runs (ADR-023).
//
// Out of reach: pixels a page draws on a canvas, background images on ::before and ::after, SVG
// <image>, <object> and <embed>, and shadow roots the browser gives no way into.

import { blobToBase64 } from "./shared/bytes";
import { dhashRgba } from "./shared/dhash";
import { ext } from "./shared/ext";
import type { FrameJudgeRequest, JudgeRequest, JudgeResponse } from "./shared/types";
import { type FrameGrab, VideoSampler } from "./shared/video";

const TIMEOUT_MS = 6000;
const NEAR_VIEWPORT = "100%"; // one viewport of margin on every side
const FRAME_SIDE = 384;
const BLUR = "blur(28px)";
/** Covers the page lifted before one tamper event is queued, once per page load. */
export const TAMPER_AFTER = 3;
const MIN_BACKGROUND_SIDE = 20;
const WAIT_POLL_MS = 150;
const SWEEP_MS = 2000;
const SWEEP_BACKGROUND_LIMIT = 4000;

export type Mark = "pending" | "safe" | "flag" | "cover";
type Kind = "img" | "video" | "background";
/** starting: holding images while the background says whether this browser is paired. */
export type Mode = "starting" | "on" | "report" | "off";
type Judgeable = HTMLImageElement | HTMLVideoElement;

interface Managed {
  kind: Kind;
  mark: Mark;
  /** The source judged, or being judged; null when a judgement is due. */
  src: string | null;
  /** Background images: the url waiting for a judgement (our override hides the computed value). */
  next: string | null;
  note: "checking" | "kept" | null;
  /** Our inline properties as the browser read them back right after we set them. */
  seen: Map<string, [string, string]>;
  /** The page's own inline values for those properties, restored when ours come off. */
  orig: Map<string, [string, string]>;
}

const managed = new WeakMap<Element, Managed>();
const live = new Set<Element>(); // pruned of detached elements by the sweep
const roots = new Set<Document | ShadowRoot>();
const waiting = new Set<HTMLImageElement>(); // held until the image has loaded
let mode: Mode = "starting";
let removals = 0;
let tamperSent = false;

// Inline styles: what each decision needs, applied and checked.

function wanted(m: Managed): Record<string, string> {
  const hold = m.mark === "pending" && mode !== "report";
  if (m.kind === "background") return hold || m.mark === "cover" ? { "background-image": "none" } : {};
  if (m.mark === "cover") {
    return { filter: m.note === "checking" ? `${BLUR} brightness(0.8)` : BLUR, "clip-path": "inset(0)" };
  }
  if (hold && m.kind === "img") return { visibility: "hidden" };
  return {};
}

function styleOf(el: Element): CSSStyleDeclaration | null {
  return (el as HTMLElement).style ?? null;
}

function apply(el: Element, m: Managed): void {
  const style = styleOf(el);
  if (!style) return;
  const want = wanted(m);
  for (const [prop, value] of Object.entries(want)) {
    if (!m.orig.has(prop)) {
      const before: [string, string] = [style.getPropertyValue(prop), style.getPropertyPriority(prop)];
      // A clone of an element we covered carries our value; that is not the page's own.
      m.orig.set(prop, before[1] === "important" && before[0] === value ? ["", ""] : before);
    }
    style.setProperty(prop, value, "important");
    m.seen.set(prop, [style.getPropertyValue(prop), style.getPropertyPriority(prop)]);
  }
  for (const prop of Array.from(m.seen.keys())) {
    if (prop in want) continue;
    const [value, priority] = m.orig.get(prop) ?? ["", ""];
    if (value) style.setProperty(prop, value, priority);
    else style.removeProperty(prop);
    m.seen.delete(prop);
    m.orig.delete(prop);
  }
  if (m.kind !== "background" && el.getAttribute("data-wm") !== m.mark) el.setAttribute("data-wm", m.mark);
}

function intact(el: Element, m: Managed): boolean {
  const style = styleOf(el);
  if (!style) return true;
  for (const [prop, [value, priority]] of m.seen) {
    if (style.getPropertyValue(prop) !== value || style.getPropertyPriority(prop) !== priority) return false;
  }
  return true;
}

function holds(m: Managed): boolean {
  return Object.keys(wanted(m)).length > 0;
}

function ensure(el: Element, kind: Kind): Managed {
  let m = managed.get(el);
  if (!m) {
    m = { kind, mark: "safe", src: null, next: null, note: null, seen: new Map(), orig: new Map() };
    managed.set(el, m);
  }
  live.add(el);
  return m;
}

function setMark(el: Element, mark: Mark): void {
  const m = managed.get(el);
  if (!m) return;
  m.mark = mark;
  apply(el, m);
}

/** The decision this script holds for an element (for the tests and the e2e check). */
export function stateOf(el: Element): Mark | undefined {
  return managed.get(el)?.mark;
}

/** The page lifted a cover or a hold; it was put back. Three on one page load is one tamper event. */
function noteRemoval(): void {
  removals += 1;
  if (removals < TAMPER_AFTER || tamperSent) return;
  tamperSent = true;
  void Promise.resolve(
    ext.runtime.sendMessage({ type: "tamper", reason: "page_unblur", count: removals, url: location.href }),
  ).catch(() => undefined);
}

export function removalCount(): number {
  return removals;
}

// Images

function sourceOf(img: HTMLImageElement): string {
  return img.currentSrc || img.src || "";
}

export function shouldJudge(img: HTMLImageElement): boolean {
  const src = sourceOf(img);
  if (!src) return false;
  return managed.get(img)?.src !== src;
}

async function askBackground<T extends JudgeResponse>(req: JudgeRequest | FrameJudgeRequest): Promise<T> {
  const timeout = new Promise<T>((resolve) =>
    setTimeout(() => resolve({ decision: "cover", hash: null, score: null, reason: "error" } as T), TIMEOUT_MS),
  );
  const answer = ext.runtime.sendMessage(req) as Promise<T>;
  return Promise.race([answer, timeout]);
}

function asMark(res: JudgeResponse): Mark {
  return res.decision === "safe" ? "safe" : res.decision === "flag" ? "flag" : "cover";
}

async function judgeImage(img: HTMLImageElement): Promise<void> {
  const m = managed.get(img);
  if (!m || mode === "starting" || mode === "off") return;
  const src = sourceOf(img);
  if (!src || m.src === src) return;
  if (!img.complete || img.naturalWidth === 0) {
    consider(img);
    return;
  }
  m.src = src;
  setMark(img, "pending");
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
  if (m.src !== src || sourceOf(img) !== src) return; // the image changed while we waited
  setMark(img, asMark(res));
}

/** Hold the image and judge it once it has loaded and is near the viewport. Reads only what the
 * browser says about the image (complete, naturalWidth, currentSrc), never what the page wrote. */
function consider(img: HTMLImageElement): void {
  if (mode === "off") return;
  const m = ensure(img, "img");
  const src = sourceOf(img);
  if (src && m.src === src) return; // judged, or being judged
  if (!src) {
    m.src = null;
    setMark(img, "safe"); // nothing to show; a src set later is a mutation and comes back here
    return;
  }
  m.src = null;
  setMark(img, "pending");
  if (mode === "starting") return;
  if (!img.complete) {
    waitForLoad(img);
    return;
  }
  if (img.naturalWidth > 0) {
    whenNear(img);
    return;
  }
  m.src = src; // broken, or no intrinsic size: nothing a model could see
  setMark(img, "safe");
}

/** src, srcset or a <picture> source changed: hold the image until the new one is judged. */
function sourceChanged(img: HTMLImageElement): void {
  if (mode === "off") return;
  const m = ensure(img, "img");
  m.src = null;
  setMark(img, "pending");
  if (mode === "starting") return;
  if (img.complete) consider(img);
  else waitForLoad(img);
}

// Load events can be stopped by the page (a capturing listener on window runs first), so a poll on
// `complete` backs them up.
let waitTimer: ReturnType<typeof setInterval> | null = null;

function waitForLoad(img: HTMLImageElement): void {
  waiting.add(img);
  waitTimer ??= setInterval(pollWaiting, WAIT_POLL_MS);
}

function pollWaiting(): void {
  for (const img of Array.from(waiting)) {
    if (!img.isConnected) {
      waiting.delete(img);
      continue;
    }
    if (img.complete) {
      waiting.delete(img);
      consider(img);
    }
  }
  if (waiting.size === 0 && waitTimer) {
    clearInterval(waitTimer);
    waitTimer = null;
  }
}

function onLoad(e: Event): void {
  const t = e.target;
  if (!(t instanceof HTMLImageElement) || mode === "off") return;
  waiting.delete(t);
  consider(t); // a no-op unless the browser says the source changed
}

// Background images

function backgroundUrl(value: string): string | null {
  const m = /url\(\s*(['"]?)(.*?)\1\s*\)/.exec(value);
  return m && m[2] ? m[2] : null;
}

const backgroundQueue = new Set<Element>();
let backgroundScheduled = false;

function queueBackground(el: Element): void {
  if (mode !== "on" && mode !== "report") return;
  if (el instanceof HTMLImageElement || el instanceof HTMLVideoElement) return;
  backgroundQueue.add(el);
  if (backgroundScheduled) return;
  backgroundScheduled = true;
  const run = (): void => {
    backgroundScheduled = false;
    const batch = Array.from(backgroundQueue);
    backgroundQueue.clear();
    for (const b of batch) considerBackground(b);
  };
  // Before the next paint where the browser allows it.
  if (typeof requestAnimationFrame === "function" && !document.hidden) requestAnimationFrame(run);
  else setTimeout(run, 50);
}

function considerBackground(el: Element): void {
  if (mode !== "on" && mode !== "report") return;
  if (!(el instanceof HTMLElement) || !el.isConnected) return;
  const m0 = managed.get(el);
  if (m0 && m0.kind !== "background") return;
  if (m0 && m0.mark !== "safe" && m0.mark !== "flag") return; // held or covered until judged
  let url: string | null;
  try {
    url = backgroundUrl(getComputedStyle(el).backgroundImage || "");
  } catch {
    return;
  }
  if (!url || (m0 && m0.src === url)) return;
  const r = el.getBoundingClientRect();
  if (r.width < MIN_BACKGROUND_SIDE || r.height < MIN_BACKGROUND_SIDE) return; // icons, sprites, hidden
  const m = ensure(el, "background");
  m.next = url;
  m.src = null;
  setMark(el, "pending");
  whenNear(el);
}

async function judgeBackground(el: HTMLElement): Promise<void> {
  const m = managed.get(el);
  if (!m || m.kind !== "background" || !m.next || mode === "starting" || mode === "off") return;
  const url = m.next;
  if (m.src === url) return;
  m.src = url;
  const r = el.getBoundingClientRect();
  const res = await askBackground({
    type: "judge",
    url,
    width: Math.round(r.width),
    height: Math.round(r.height),
    displayWidth: Math.round(r.width),
    displayHeight: Math.round(r.height),
    pageUrl: location.href,
    pageTitle: document.title,
  });
  if (m.src !== url) return;
  setMark(el, asMark(res));
  if (m.mark !== "cover") queueBackground(el); // the page may have swapped it while ours hid it
}

// Viewport gating. Without IntersectionObserver (tests, very old engines) everything is judged at once.

let near: IntersectionObserver | null | undefined;
const onScreen = new WeakSet<Element>();

function judgeElement(el: Element): void {
  if (el instanceof HTMLImageElement) void judgeImage(el);
  else if (managed.get(el)?.kind === "background") void judgeBackground(el as HTMLElement);
}

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
        judgeElement(e.target);
      }
    },
    { rootMargin: NEAR_VIEWPORT },
  );
  return near;
}

/** Judge now if the element is near the viewport, otherwise when it gets there. */
function whenNear(el: Element): void {
  const io = nearObserver();
  if (!io) {
    judgeElement(el);
    return;
  }
  io.observe(el);
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
  ensure(v, "video").src = source;
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
  isCovered: (v) => managed.get(v)?.mark === "cover",
  mark: (v, decision) => {
    ensure(v, "video");
    setMark(v, decision);
  },
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
  if (mode === "off" || mode === "starting") return;
  if (sampler.isTracked(v) || sampler.isUnsampleable(v)) return;
  ensure(v, "video");
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

// Finding everything: the document, open shadow roots (closed ones too where the browser offers a
// way in to extensions), and every change to them.

function shadowOf(el: Element): ShadowRoot | null {
  try {
    const g = globalThis as unknown as { chrome?: { dom?: { openOrClosedShadowRoot?: (e: Element) => ShadowRoot | null } } };
    const dom = g.chrome?.dom;
    if (dom?.openOrClosedShadowRoot && el instanceof HTMLElement) return dom.openOrClosedShadowRoot(el) ?? null;
  } catch {
    // not a host
  }
  const firefox = (el as Element & { openOrClosedShadowRoot?: () => ShadowRoot | null }).openOrClosedShadowRoot;
  if (typeof firefox === "function") {
    try {
      return firefox.call(el) ?? null;
    } catch {
      // not a host
    }
  }
  return el.shadowRoot;
}

const OBSERVE: MutationObserverInit = {
  childList: true,
  subtree: true,
  attributes: true,
  attributeFilter: ["src", "srcset", "sizes", "media", "type", "style", "class", "data-wm"],
};
let observer: MutationObserver | null = null;
let sweepTimer: ReturnType<typeof setInterval> | null = null;

function adopt(root: ShadowRoot): void {
  if (roots.has(root)) return;
  roots.add(root);
  observer?.observe(root, OBSERVE);
  root.addEventListener("load", onLoad, true);
  scan(root);
}

function scan(root: ParentNode): void {
  if (root instanceof HTMLImageElement) consider(root);
  else if (root instanceof HTMLVideoElement) considerVideo(root);
  root.querySelectorAll("img").forEach(consider);
  root.querySelectorAll("video").forEach(considerVideo);
  const all: Element[] = Array.from(root.querySelectorAll("*"));
  if (root instanceof Element) all.unshift(root);
  for (const el of all) {
    const sr = shadowOf(el);
    if (sr) adopt(sr);
    queueBackground(el);
  }
}

function pictureChanged(picture: Element): void {
  const img = picture.querySelector("img");
  if (img) sourceChanged(img);
}

/** The page put wm-off on <html>, which lifts the stylesheet's hold. */
function guardRootClass(undone: Set<Element>): void {
  const html = document.documentElement;
  if (mode === "off" || !html?.classList.contains("wm-off")) return;
  html.classList.remove("wm-off");
  undone.add(html);
}

function onAttribute(r: MutationRecord, undone: Set<Element>): void {
  const t = r.target as Element;
  const name = r.attributeName;
  const m = managed.get(t);
  if (name === "style" || name === "data-wm") {
    if (m) {
      if (!intact(t, m)) {
        apply(t, m);
        if (holds(m)) undone.add(t);
      } else if (m.kind !== "background" && t.getAttribute("data-wm") !== m.mark) {
        t.setAttribute("data-wm", m.mark);
      }
      if (m.kind === "background" && name === "style") queueBackground(t);
    } else if (name === "style") {
      queueBackground(t);
    }
    return;
  }
  if (name === "class") {
    if (t === document.documentElement) guardRootClass(undone);
    queueBackground(t);
    return;
  }
  // src, srcset, sizes, media, type
  if (t instanceof HTMLImageElement) {
    if (name === "src" || name === "srcset" || name === "sizes") sourceChanged(t);
    return;
  }
  if (t instanceof HTMLSourceElement && t.parentElement instanceof HTMLPictureElement) pictureChanged(t.parentElement);
}

function onMutations(records: MutationRecord[]): void {
  if (mode === "off") return;
  const undone = new Set<Element>();
  for (const r of records) {
    if (r.type === "attributes") {
      onAttribute(r, undone);
      continue;
    }
    r.addedNodes.forEach((n) => {
      if (n instanceof Element) scan(n);
    });
    if (r.target instanceof HTMLPictureElement) pictureChanged(r.target);
  }
  for (let i = 0; i < undone.size; i++) noteRemoval();
}

/** Every two seconds while visible: shadow roots attached after their host was inserted, sources
 * that changed without a load event reaching us, and background images a stylesheet changed. */
function sweep(): void {
  if (mode === "off" || document.hidden) return;
  for (const el of Array.from(live)) if (!el.isConnected) live.delete(el);
  for (const r of Array.from(roots)) if (r instanceof ShadowRoot && !r.host.isConnected) roots.delete(r);
  for (const el of live) {
    if (!(el instanceof HTMLImageElement)) continue;
    const m = managed.get(el);
    if (m?.src && el.complete && sourceOf(el) !== m.src) consider(el);
  }
  let n = 0;
  for (const root of Array.from(roots)) {
    for (const el of Array.from(root.querySelectorAll("*"))) {
      const sr = shadowOf(el);
      if (sr) adopt(sr);
      if (n++ < SWEEP_BACKGROUND_LIMIT) queueBackground(el);
    }
  }
}

/** Start holding and watching. Idempotent. Runs before the background has answered, so nothing
 * slips through while it does. */
function watch(): void {
  if (observer) return;
  observer = new MutationObserver(onMutations);
  roots.add(document);
  observer.observe(document, OBSERVE);
  document.addEventListener("load", onLoad, true);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) sampler.stop();
    else if (sampler.size > 0) sampler.start();
  });
  sweepTimer = setInterval(sweep, SWEEP_MS);
}

/** What the background said: on (cover), report (judge and report, hold nothing back) or off (never
 * paired: let everything through and stop watching). */
export function activate(next: Mode): void {
  mode = next;
  if (next === "off") {
    release();
    return;
  }
  document.documentElement?.classList.remove("wm-off", "wm-report");
  for (const el of Array.from(live)) {
    const m = managed.get(el);
    if (m) apply(el, m); // report mode lifts the holds
  }
  for (const root of Array.from(roots)) scan(root);
  if (roots.size === 0) scan(document);
}

function release(): void {
  observer?.disconnect();
  observer = null;
  if (sweepTimer) clearInterval(sweepTimer);
  sweepTimer = null;
  if (waitTimer) clearInterval(waitTimer);
  waitTimer = null;
  waiting.clear();
  sampler.stop();
  for (const el of Array.from(live)) {
    const m = managed.get(el);
    if (m) {
      m.mark = "safe";
      m.note = null;
      apply(el, m);
    }
    el.removeAttribute("data-wm");
    managed.delete(el);
  }
  live.clear();
  document.documentElement?.classList.add("wm-off");
}

/** Every image on the page with its natural size, for the popup's page scorer. */
export function listImages(): { url: string; width: number; height: number }[] {
  const seen = new Set<string>();
  const out: { url: string; width: number; height: number }[] = [];
  const all = new Set<Document | ShadowRoot>([document, ...roots]);
  for (const root of all) {
    root.querySelectorAll("img").forEach((img) => {
      const src = sourceOf(img);
      if (!src || seen.has(src)) return;
      seen.add(src);
      out.push({ url: src, width: img.naturalWidth, height: img.naturalHeight });
    });
  }
  return out;
}

/** A click on a covered image or video: keep it covered, ask the server to look again, show the answer. */
export async function recheck(el: Judgeable): Promise<void> {
  const m = managed.get(el);
  const url = m?.src || el.currentSrc || el.src;
  if (!m || !url) return;
  m.note = "checking";
  apply(el, m);
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
    m.note = null;
    revise(url, "safe");
    setMark(el, "safe");
    el.title = "";
    return;
  }
  m.note = "kept";
  apply(el, m);
  el.title = res.reason === "known_bad" || res.reason === "explicit"
    ? "Watchman: kept covered. The household server agrees."
    : "Watchman: kept covered. The server could not be reached.";
}

document.addEventListener(
  "click",
  (e) => {
    if (!e.isTrusted) return; // a page cannot click its way into rechecks
    const target = e.target as Element | null;
    const judgeable = target instanceof HTMLImageElement || target instanceof HTMLVideoElement;
    const m = target ? managed.get(target) : undefined;
    if (!judgeable || !m || m.mark !== "cover" || m.note === "checking") return;
    e.preventDefault();
    e.stopPropagation();
    void recheck(target);
  },
  true,
);

/** The server's answer for an image or video that was covered while unsure. */
export function revise(url: string, decision: "safe" | "cover"): number {
  let n = 0;
  for (const el of Array.from(live)) {
    const m = managed.get(el);
    if (m && m.src === url) {
      setMark(el, decision);
      n++;
    }
  }
  return n;
}

ext.runtime.onMessage.addListener((raw: unknown, _sender, sendResponse) => {
  const msg = raw as { type?: string; url?: string; decision?: "safe" | "cover" };
  if (msg?.type === "list_images") { sendResponse(listImages()); return false; }
  if (msg?.type === "revise" && msg.url && msg.decision) { sendResponse(revise(msg.url, msg.decision)); return false; }
  return false;
});

async function main(): Promise<void> {
  watch(); // hold every image from the first moment, before the background has answered
  scan(document);
  type Active = { active: boolean; coverOnDevice?: boolean; videoSampleS?: number };
  let r: Active | null = null;
  try {
    r = (await ext.runtime.sendMessage({ type: "active" })) as Active | null;
  } catch {
    r = null;
  }
  if (!r?.active) {
    activate("off");
    return;
  }
  if (r.videoSampleS) sampler.setIntervalMs(r.videoSampleS * 1000);
  // Report only: images are not held back; they are still judged and reported.
  activate(r.coverOnDevice === false ? "report" : "on");
  setInterval(reportVideoStats, 60_000);
}

if (typeof document !== "undefined" && !(globalThis as { __WATCHMAN_TEST__?: boolean }).__WATCHMAN_TEST__) {
  void main();
}

export { consider, considerBackground, considerVideo, judgeImage as judge, scan, watch };
