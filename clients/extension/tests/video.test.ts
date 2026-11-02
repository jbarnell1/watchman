// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

import { type FrameGrab, isPlaying, VideoSampler } from "../src/shared/video";

function video(playing = true): HTMLVideoElement {
  const v = document.createElement("video");
  Object.defineProperty(v, "paused", { value: !playing, configurable: true });
  Object.defineProperty(v, "ended", { value: false, configurable: true });
  Object.defineProperty(v, "readyState", { value: playing ? 4 : 0, configurable: true });
  document.body.appendChild(v);
  return v;
}

function frame(hash: string): FrameGrab {
  return { hash, jpegBase64: "AA==", width: 640, height: 360 };
}

describe("video sampler", () => {
  it("judges a new frame, drops an unchanged one, and a cover sticks", async () => {
    const frames = [frame("0000000000000000"), frame("0000000000000001"), frame("ffffffffffffffff"), frame("ffffffffffffffff")];
    const grab = vi.fn(async () => frames.shift() ?? "later");
    const judge = vi.fn(async (_v: HTMLVideoElement, f: FrameGrab) => (f.hash.startsWith("f") ? "cover" : "safe") as "safe" | "cover");
    const s = new VideoSampler({ grab, judge, setInterval: (() => 1) as unknown as typeof setInterval, clearInterval: () => undefined });
    const v = video();
    s.track(v);
    expect(v.dataset.wmVideo).toBe("tracked");

    await s.tick(); // first frame: judged safe
    expect(judge).toHaveBeenCalledTimes(1);
    expect(v.dataset.wm).toBe("safe");

    await s.tick(); // one bit away: dropped before the model
    expect(judge).toHaveBeenCalledTimes(1);
    expect(s.dropped).toBe(1);

    await s.tick(); // a different frame: covered
    expect(judge).toHaveBeenCalledTimes(2);
    expect(v.dataset.wm).toBe("cover");

    await s.tick(); // covered videos are not sampled again until a tap clears them
    expect(grab).toHaveBeenCalledTimes(3);
  });

  it("leaves paused and off-screen videos alone and gives up on unsampleable ones", async () => {
    const grab = vi.fn(async () => null);
    const judge = vi.fn(async () => "safe" as const);
    let visible = false;
    const s = new VideoSampler({ grab, judge, onScreen: () => visible, setInterval: (() => 1) as unknown as typeof setInterval, clearInterval: () => undefined });
    const paused = video(false);
    const playing = video(true);
    s.track(paused);
    s.track(playing);
    await s.tick();
    expect(grab).not.toHaveBeenCalled(); // paused, and the playing one is off screen

    visible = true;
    await s.tick();
    expect(grab).toHaveBeenCalledTimes(1); // only the playing one
    expect(playing.dataset.wmVideo).toBe("unsampleable");
    expect(s.size).toBe(1); // untracked for good
    s.track(playing);
    expect(s.size).toBe(1); // and not re-tracked
  });

  it("the interval follows the policy and restarts a running timer", () => {
    const timers: number[] = [];
    let cleared = 0;
    const s = new VideoSampler({
      grab: async () => null,
      judge: async () => "safe" as const,
      setInterval: ((_: unknown, ms: number) => { timers.push(ms); return timers.length; }) as unknown as typeof setInterval,
      clearInterval: (() => { cleared++; }) as unknown as typeof clearInterval,
    });
    expect(s.interval).toBe(5000);
    s.track(video());
    expect(timers).toEqual([5000]);
    s.setIntervalMs(8000);
    expect(cleared).toBe(1);
    expect(timers).toEqual([5000, 8000]);
    s.setIntervalMs(500); // never faster than once a second
    expect(s.interval).toBe(1000);
  });

  it("isPlaying needs data and no pause", () => {
    expect(isPlaying(video(true))).toBe(true);
    expect(isPlaying(video(false))).toBe(false);
  });
});
