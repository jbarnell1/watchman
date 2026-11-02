// Formats the page scorer's output as plain lines the owner can paste into a chat: numbers only.

import type { PageScore, Thresholds } from "./types";

export function formatScores(images: PageScore[], t: Thresholds): string[] {
  const lines = [`thresholds cover ${t.cover} flag ${t.flag}; ${images.length} images`];
  for (const i of images) {
    const score = i.score === null ? "  -  " : i.score.toFixed(3);
    const mark = i.decision === "cover" ? "COVER" : i.decision === "flag" ? "flag " : i.decision === "tiny" ? "tiny " : i.decision === "error" ? "error" : "safe ";
    lines.push(`${score}  ${mark}  ${String(i.width).padStart(4)}x${String(i.height).padEnd(4)}  ${i.hash ?? "-"}  ${shorten(i.url)}`);
  }
  return lines;
}

export function shorten(url: string, max = 60): string {
  const noQuery = url.split("?")[0];
  if (noQuery.length <= max) return noQuery;
  return noQuery.slice(0, 28) + "..." + noQuery.slice(-(max - 31));
}
