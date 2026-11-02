// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { formatScores, shorten } from "../src/shared/diagnostic";

const { listImages } = await import("../src/content");

describe("page scorer", () => {
  it("lists each image once with its natural size", () => {
    document.body.innerHTML = "";
    for (const src of ["https://x/a.jpg", "https://x/b.jpg", "https://x/a.jpg"]) {
      const img = document.createElement("img");
      Object.defineProperty(img, "naturalWidth", { value: 48 });
      Object.defineProperty(img, "naturalHeight", { value: 48 });
      Object.defineProperty(img, "currentSrc", { value: src });
      img.src = src;
      document.body.appendChild(img);
    }
    const list = listImages();
    expect(list.map((i) => i.url)).toEqual(["https://x/a.jpg", "https://x/b.jpg"]);
    expect(list[0].width).toBe(48);
  });

  it("formats numbers only, highest first as given, with shortened urls", () => {
    const lines = formatScores(
      [
        { url: "https://yt3.ggpht.com/ytc/" + "a".repeat(80) + "=s48", width: 48, height: 48, hash: "9f3a0c1e5b7d2a46", score: 0.912, decision: "cover" },
        { url: "https://x/i.png", width: 12, height: 12, hash: null, score: null, decision: "tiny" },
      ],
      { cover: 0.8, flag: 0.6, detector_cover: 0.55 },
    );
    expect(lines[0]).toBe("thresholds cover 0.8 flag 0.6; 2 images");
    expect(lines[1]).toContain("0.912  COVER    48x48    9f3a0c1e5b7d2a46");
    expect(lines[1].length).toBeLessThan(150);
    expect(lines[2]).toContain("  -    tiny ");
    expect(shorten("https://short/x")).toBe("https://short/x");
  });
});
