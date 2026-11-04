// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fakeChrome } from "./setup";

type Content = typeof import("../src/content");

const { activate, consider, recheck, revise, scan, shouldJudge, stateOf } = await import("../src/content");

function img(src: string, natural = 100, attach: ParentNode = document.body): HTMLImageElement {
  const el = document.createElement("img");
  Object.defineProperty(el, "naturalWidth", { value: natural, configurable: true });
  Object.defineProperty(el, "naturalHeight", { value: natural, configurable: true });
  Object.defineProperty(el, "complete", { value: true, configurable: true });
  Object.defineProperty(el, "currentSrc", { value: src, configurable: true });
  el.src = src;
  attach.appendChild(el);
  return el;
}

/** Point an image at a new source the way a browser would report it once loaded. */
function swap(el: HTMLImageElement, src: string): void {
  Object.defineProperty(el, "currentSrc", { value: src, configurable: true });
  el.src = src;
}

async function settle(): Promise<void> {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
}

function sent(): Record<string, unknown>[] {
  return (fakeChrome.runtime.sendMessage.mock.calls as unknown as [Record<string, unknown>][]).map((c) => c[0]);
}

describe("content script", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    fakeChrome.runtime.sendMessage.mockReset();
    activate("on");
  });

  it("marks an image pending, then with the background's decision", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", hash: "aa", score: 0.95, reason: "scored" });
    const el = img("https://x/a.jpg");
    consider(el);
    expect(stateOf(el)).toBe("pending");
    expect(el.style.getPropertyValue("visibility")).toBe("hidden");
    expect(el.style.getPropertyPriority("visibility")).toBe("important");
    await settle();
    expect(stateOf(el)).toBe("cover");
    expect(el.dataset.wm).toBe("cover"); // a mirror, for the stylesheet and debugging
    expect(el.style.getPropertyValue("filter")).toContain("blur");
    expect(el.style.getPropertyValue("visibility")).toBe("");
    const req = sent()[0] as { type: string; url: string; width: number };
    expect(req.type).toBe("judge");
    expect(req.url).toBe("https://x/a.jpg");
    expect(req.width).toBe(100);
  });

  it("does not judge the same source twice", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "safe", hash: null, score: null, reason: "scored" });
    const el = img("https://x/b.jpg");
    consider(el);
    await settle();
    expect(shouldJudge(el)).toBe(false);
    consider(el);
    expect(fakeChrome.runtime.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("a marker the page wrote does not count as judged", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "cover", hash: "aa", score: 0.95, reason: "scored" });
    const el = img("https://x/premarked.jpg");
    el.setAttribute("data-wm", "safe");
    el.setAttribute("data-wm-src", "https://x/premarked.jpg");
    expect(shouldJudge(el)).toBe(true);
    consider(el);
    await settle();
    expect(fakeChrome.runtime.sendMessage).toHaveBeenCalledTimes(1);
    expect(stateOf(el)).toBe("cover");
  });

  it("scan finds every image in a subtree", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "safe", hash: null, score: null, reason: "scored" });
    img("https://x/1.jpg");
    img("https://x/2.jpg");
    scan(document);
    await settle();
    expect(fakeChrome.runtime.sendMessage).toHaveBeenCalledTimes(2);
    expect(document.querySelectorAll('img[data-wm="safe"]').length).toBe(2);
  });

  it("scan finds images inside open shadow roots", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "cover", hash: "aa", score: 0.95, reason: "scored" });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = host.attachShadow({ mode: "open" });
    const inner = img("https://x/shadow.jpg", 100, root);
    scan(document);
    await settle();
    expect(sent().some((m) => m.url === "https://x/shadow.jpg")).toBe(true);
    expect(stateOf(inner)).toBe("cover");
    expect(inner.style.getPropertyValue("filter")).toContain("blur");
  });

  it("a revise message changes the mark of the image it names", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "cover", hash: "aa", score: 0.7, reason: "asking" });
    const el = img("https://x/unsure.jpg");
    consider(el);
    await settle();
    expect(stateOf(el)).toBe("cover");
    expect(revise("https://x/unsure.jpg", "safe")).toBe(1);
    expect(stateOf(el)).toBe("safe");
    expect(el.style.getPropertyValue("filter")).toBe("");
    expect(revise("https://x/other.jpg", "safe")).toBe(0);
  });

  it("a tap on a covered image asks again and unblurs on a safe answer", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", hash: "aa", score: 0.7, reason: "asking" });
    const el = img("https://x/tap.jpg");
    consider(el);
    await settle();
    expect(stateOf(el)).toBe("cover");
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "safe", reason: "scored" });
    await recheck(el);
    expect(stateOf(el)).toBe("safe");
    expect(sent()[1]).toMatchObject({ type: "recheck", url: "https://x/tap.jpg" });
    // Kept covered when the server agrees, with the reason in the tooltip.
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", hash: "bb", score: 0.7, reason: "asking" });
    const el2 = img("https://x/tap2.jpg");
    consider(el2);
    await settle();
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", reason: "explicit" });
    await recheck(el2);
    expect(stateOf(el2)).toBe("cover");
    expect(el2.title).toContain("kept covered");
  });

  it("an image that has not loaded is held, and a load event the page fakes does not release it", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "safe", hash: null, score: null, reason: "tiny" });
    const el = document.createElement("img");
    Object.defineProperty(el, "complete", { value: false, configurable: true });
    Object.defineProperty(el, "naturalWidth", { value: 0, configurable: true });
    Object.defineProperty(el, "currentSrc", { value: "https://x/slow-load.jpg", configurable: true });
    el.src = "https://x/slow-load.jpg";
    document.body.appendChild(el);
    consider(el);
    el.dispatchEvent(new Event("load"));
    await settle();
    expect(fakeChrome.runtime.sendMessage).not.toHaveBeenCalled();
    expect(stateOf(el)).toBe("pending");
    expect(el.style.getPropertyValue("visibility")).toBe("hidden");
  });

  it("with an IntersectionObserver an image is judged only once it is near the viewport", async () => {
    const observed: Element[] = [];
    let fire: (entries: Partial<IntersectionObserverEntry>[]) => void = () => undefined;
    class FakeIO {
      constructor(cb: IntersectionObserverCallback) {
        fire = (entries) => cb(entries as IntersectionObserverEntry[], this as unknown as IntersectionObserver);
      }
      observe(el: Element) { observed.push(el); }
      unobserve() { }
      disconnect() { }
    }
    (globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver = FakeIO;
    vi.resetModules();
    const fresh = await import("../src/content");
    fresh.activate("on");
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "safe", hash: null, score: null, reason: "scored" });
    const far = img("https://x/far.jpg");
    fresh.consider(far);
    await settle();
    expect(observed).toContain(far);
    expect(fakeChrome.runtime.sendMessage).not.toHaveBeenCalled();
    expect(fresh.stateOf(far)).toBe("pending"); // held, and off screen anyway
    expect(far.style.getPropertyValue("visibility")).toBe("hidden");
    fire([{ isIntersecting: true, target: far, boundingClientRect: { top: 900 } as DOMRectReadOnly }]);
    await settle();
    expect(fakeChrome.runtime.sendMessage).toHaveBeenCalledTimes(1);
    expect(fresh.stateOf(far)).toBe("safe");
    delete (globalThis as unknown as { IntersectionObserver?: unknown }).IntersectionObserver;
    vi.resetModules();
  });

  it("covers when the background never answers", async () => {
    vi.useFakeTimers();
    fakeChrome.runtime.sendMessage.mockReturnValue(new Promise(() => {}));
    const el = img("https://x/slow.jpg");
    consider(el);
    await vi.advanceTimersByTimeAsync(6500);
    expect(stateOf(el)).toBe("cover");
    vi.useRealTimers();
  });
});

describe("a page that fights the cover", () => {
  let c: Content;

  beforeEach(async () => {
    document.body.innerHTML = "";
    document.documentElement.className = "";
    fakeChrome.runtime.sendMessage.mockReset();
    vi.resetModules();
    c = await import("../src/content");
    c.watch();
    c.activate("on");
  });

  afterEach(() => {
    c.activate("off");
    document.documentElement.className = "";
  });

  async function covered(src: string): Promise<HTMLImageElement> {
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", hash: "aa", score: 0.95, reason: "scored" });
    const el = img(src);
    await settle();
    expect(c.stateOf(el)).toBe("cover");
    return el;
  }

  it("gets the blur back when it removes the style, and a tamper event after three removals", async () => {
    const el = await covered("https://x/fight.jpg");
    el.removeAttribute("style");
    await settle();
    expect(el.style.getPropertyValue("filter")).toContain("blur");
    expect(el.style.getPropertyPriority("filter")).toBe("important");

    el.style.setProperty("filter", "none", "important");
    await settle();
    expect(el.style.getPropertyValue("filter")).toContain("blur");
    expect(sent().some((m) => m.type === "tamper")).toBe(false);

    el.style.cssText = "";
    await settle();
    expect(el.style.getPropertyValue("filter")).toContain("blur");
    const tamper = sent().filter((m) => m.type === "tamper");
    expect(tamper).toHaveLength(1);
    expect(tamper[0]).toMatchObject({ type: "tamper", reason: "page_unblur", count: 3, url: location.href });

    // Once per page load.
    el.removeAttribute("style");
    await settle();
    expect(sent().filter((m) => m.type === "tamper")).toHaveLength(1);
    expect(c.removalCount()).toBe(4);
  });

  it("gets the hold back on an image that is still being judged", async () => {
    fakeChrome.runtime.sendMessage.mockReturnValueOnce(new Promise(() => {}));
    const el = img("https://x/held.jpg");
    await settle();
    expect(c.stateOf(el)).toBe("pending");
    el.style.setProperty("visibility", "visible", "important");
    await settle();
    expect(el.style.getPropertyValue("visibility")).toBe("hidden");
    expect(c.removalCount()).toBe(1);
  });

  it("writing the marker attribute changes nothing", async () => {
    const el = await covered("https://x/marker.jpg");
    el.setAttribute("data-wm", "safe");
    el.removeAttribute("data-wm");
    await settle();
    expect(c.stateOf(el)).toBe("cover");
    expect(el.getAttribute("data-wm")).toBe("cover");
    expect(el.style.getPropertyValue("filter")).toContain("blur");
    expect(c.removalCount()).toBe(0); // the cover itself was never lifted
  });

  it("wm-off on <html> is taken off again", async () => {
    document.documentElement.classList.add("wm-off");
    await settle();
    expect(document.documentElement.classList.contains("wm-off")).toBe(false);
  });

  it("a new src is held and judged again", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "safe", hash: null, score: 0.1, reason: "scored" });
    const el = img("https://x/first.jpg");
    await settle();
    expect(c.stateOf(el)).toBe("safe");
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", hash: "bb", score: 0.95, reason: "scored" });
    swap(el, "https://x/second.jpg");
    await settle();
    const urls = sent().filter((m) => m.type === "judge").map((m) => m.url);
    expect(urls).toEqual(["https://x/first.jpg", "https://x/second.jpg"]);
    expect(c.stateOf(el)).toBe("cover");
  });

  it("a page that writes the old source into a marker does not skip the new judgement", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "safe", hash: null, score: 0.1, reason: "scored" });
    const el = img("https://x/old.jpg");
    await settle();
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", hash: "bb", score: 0.95, reason: "scored" });
    el.dataset.wmSrc = "https://x/new.jpg";
    el.dataset.wm = "safe";
    swap(el, "https://x/new.jpg");
    await settle();
    expect(c.stateOf(el)).toBe("cover");
  });

  it("a <picture> source change holds the image and judges it again", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "safe", hash: null, score: 0.1, reason: "scored" });
    const picture = document.createElement("picture");
    const source = document.createElement("source");
    source.srcset = "https://x/small.jpg";
    picture.appendChild(source);
    document.body.appendChild(picture);
    const el = img("https://x/small.jpg", 100, picture);
    await settle();
    expect(c.stateOf(el)).toBe("safe");
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "cover", hash: "cc", score: 0.95, reason: "scored" });
    Object.defineProperty(el, "currentSrc", { value: "https://x/swapped.jpg", configurable: true });
    source.srcset = "https://x/swapped.jpg";
    await settle();
    expect(sent().filter((m) => m.type === "judge").map((m) => m.url)).toContain("https://x/swapped.jpg");
    expect(c.stateOf(el)).toBe("cover");
  });

  it("an image added inside a shadow root later is found", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "cover", hash: "aa", score: 0.95, reason: "scored" });
    const host = document.createElement("div");
    const root = host.attachShadow({ mode: "open" });
    document.body.appendChild(host);
    await settle();
    const inner = img("https://x/late-shadow.jpg", 100, root);
    await settle();
    expect(c.stateOf(inner)).toBe("cover");
  });

  it("a CSS background image is judged and hidden when covered", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "cover", hash: "aa", score: 0.95, reason: "scored" });
    const div = document.createElement("div");
    div.getBoundingClientRect = () => ({ width: 300, height: 200, top: 0, left: 0, right: 300, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
    div.style.backgroundImage = 'url("https://x/bg.jpg")';
    document.body.appendChild(div);
    await new Promise((r) => setTimeout(r, 120));
    await settle();
    expect(sent().some((m) => m.type === "judge" && m.url === "https://x/bg.jpg")).toBe(true);
    expect(c.stateOf(div)).toBe("cover");
    expect(div.style.getPropertyValue("background-image")).toBe("none");
  });
});
