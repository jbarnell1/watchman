// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeChrome } from "./setup";

const { consider, recheck, revise, scan, shouldJudge } = await import("../src/content");

function img(src: string, natural = 100): HTMLImageElement {
  const el = document.createElement("img");
  Object.defineProperty(el, "naturalWidth", { value: natural, configurable: true });
  Object.defineProperty(el, "naturalHeight", { value: natural, configurable: true });
  Object.defineProperty(el, "complete", { value: true, configurable: true });
  Object.defineProperty(el, "currentSrc", { value: src, configurable: true });
  el.src = src;
  document.body.appendChild(el);
  return el;
}

async function settle(): Promise<void> {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
}

describe("content script", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    fakeChrome.runtime.sendMessage.mockReset();
  });

  it("marks an image pending, then with the background's decision", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", hash: "aa", score: 0.95, reason: "scored" });
    const el = img("https://x/a.jpg");
    consider(el);
    expect(el.dataset.wm).toBe("pending");
    await settle();
    expect(el.dataset.wm).toBe("cover");
    const req = (fakeChrome.runtime.sendMessage.mock.calls as unknown as [unknown][])[0][0] as { type: string; url: string; width: number };
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

  it("scan finds every image in a subtree", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "safe", hash: null, score: null, reason: "scored" });
    img("https://x/1.jpg");
    img("https://x/2.jpg");
    scan(document);
    await settle();
    expect(fakeChrome.runtime.sendMessage).toHaveBeenCalledTimes(2);
    expect(document.querySelectorAll('img[data-wm="safe"]').length).toBe(2);
  });

  it("a revise message changes the mark of the image it names", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "cover", hash: "aa", score: 0.7, reason: "asking" });
    const el = img("https://x/unsure.jpg");
    consider(el);
    await settle();
    expect(el.dataset.wm).toBe("cover");
    expect(revise("https://x/unsure.jpg", "safe")).toBe(1);
    expect(el.dataset.wm).toBe("safe");
    expect(revise("https://x/other.jpg", "safe")).toBe(0);
  });

  it("a tap on a covered image asks again and unblurs on a safe answer", async () => {
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", hash: "aa", score: 0.7, reason: "asking" });
    const el = img("https://x/tap.jpg");
    consider(el);
    await settle();
    expect(el.dataset.wm).toBe("cover");
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "safe", reason: "scored" });
    await recheck(el);
    expect(el.dataset.wm).toBe("safe");
    const req = (fakeChrome.runtime.sendMessage.mock.calls as unknown as [unknown][])[1][0] as { type: string; url: string };
    expect(req).toMatchObject({ type: "recheck", url: "https://x/tap.jpg" });
    // Kept covered when the server agrees, with the reason in the tooltip.
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", hash: "bb", score: 0.7, reason: "asking" });
    const el2 = img("https://x/tap2.jpg");
    consider(el2);
    await settle();
    fakeChrome.runtime.sendMessage.mockResolvedValueOnce({ decision: "cover", reason: "explicit" });
    await recheck(el2);
    expect(el2.dataset.wm).toBe("cover");
    expect(el2.title).toContain("kept covered");
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
    fakeChrome.runtime.sendMessage.mockResolvedValue({ decision: "safe", hash: null, score: null, reason: "scored" });
    const far = img("https://x/far.jpg");
    fresh.consider(far);
    await settle();
    expect(observed).toContain(far);
    expect(fakeChrome.runtime.sendMessage).not.toHaveBeenCalled();
    expect(far.dataset.wm).toBeUndefined(); // still hidden by the stylesheet, and off screen anyway
    fire([{ isIntersecting: true, target: far, boundingClientRect: { top: 900 } as DOMRectReadOnly }]);
    await settle();
    expect(fakeChrome.runtime.sendMessage).toHaveBeenCalledTimes(1);
    expect(far.dataset.wm).toBe("safe");
    delete (globalThis as unknown as { IntersectionObserver?: unknown }).IntersectionObserver;
    vi.resetModules();
  });

  it("covers when the background never answers", async () => {
    vi.useFakeTimers();
    fakeChrome.runtime.sendMessage.mockReturnValue(new Promise(() => {}));
    const el = img("https://x/slow.jpg");
    consider(el);
    await vi.advanceTimersByTimeAsync(6500);
    expect(el.dataset.wm).toBe("cover");
    vi.useRealTimers();
  });
});
