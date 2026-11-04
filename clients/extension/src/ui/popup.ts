import { formatScores } from "../shared/diagnostic";
import { ext } from "../shared/ext";
import type { PageScore, Thresholds } from "../shared/types";

interface Status {
  paired: boolean;
  rejected?: boolean;
  server: string | null;
  serverName: string | null;
  pausedUntil: string | null;
  queued: number;
  counters: { scored: number; covered: number; flagged: number };
  work?: string;
}

const REJECTED = "The server no longer accepts this browser. Protection stays on. Pair again with a new code.";

function el(id: string): HTMLElement {
  return document.getElementById(id)!;
}

async function render(): Promise<void> {
  const s = (await ext.runtime.sendMessage({ type: "status" })) as Status;
  const state = el("state");
  const sub = el("sub");
  if (!s.paired) {
    state.textContent = "Not paired";
    state.className = "state bad";
    sub.textContent = "Open Settings and enter a pairing code from the household server.";
    return;
  }
  if (s.rejected) {
    state.textContent = "The server no longer accepts this browser";
    state.className = "state bad";
    sub.textContent = REJECTED;
  } else if (s.pausedUntil && new Date(s.pausedUntil) > new Date()) {
    state.textContent = "Paused by a partner code";
    state.className = "state warn";
    sub.textContent = `Until ${new Date(s.pausedUntil).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.`;
  } else {
    state.textContent = "Protection on";
    state.className = "state ok";
    sub.textContent = `Images are judged here before they are shown. Reporting to ${s.serverName ?? s.server}.`;
  }
  el("strip").hidden = false;
  el("diag").hidden = false;
  el("scored").textContent = String(s.counters.scored);
  el("covered").textContent = String(s.counters.covered);
  el("flagged").textContent = String(s.counters.flagged);
  const work = document.getElementById("work");
  if (work) work.textContent = s.work ?? "";
  el("queue").textContent = s.queued ? `${s.queued} event${s.queued === 1 ? "" : "s"} waiting to send.` : "";
}

function renderScores(res: { ok: boolean; error?: string; images?: PageScore[]; thresholds?: Thresholds }): void {
  const out = el("diag-out");
  out.textContent = "";
  if (!res.ok || !res.images) {
    out.textContent = res.error ?? "could not score this page";
    return;
  }
  const lines = formatScores(res.images, res.thresholds!);
  const pre = document.createElement("pre");
  pre.className = "scores";
  pre.textContent = lines.join("\n");
  const copy = document.createElement("button");
  copy.className = "btn small";
  copy.textContent = "Copy";
  copy.addEventListener("click", () => {
    navigator.clipboard.writeText(lines.join("\n")).catch(() => {});
    copy.textContent = "Copied";
  });
  out.appendChild(copy);
  out.appendChild(pre);
}

el("score-page").addEventListener("click", async () => {
  el("diag-out").textContent = "Scoring";
  const res = (await ext.runtime.sendMessage({ type: "score_page" })) as { ok: boolean; error?: string; images?: PageScore[]; thresholds?: Thresholds };
  renderScores(res);
});

el("options").addEventListener("click", (e) => {
  e.preventDefault();
  void ext.runtime.openOptionsPage();
});
void render();
