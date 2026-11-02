import { ext } from "../shared/ext";

function el<T extends HTMLElement = HTMLElement>(id: string): T {
  return document.getElementById(id) as T;
}

async function render(): Promise<void> {
  const s = (await ext.runtime.sendMessage({ type: "status" })) as {
    paired: boolean;
    server: string | null;
    serverName: string | null;
    pausedUntil: string | null;
  };
  el("pair-panel").hidden = s.paired;
  el("paired-panel").hidden = !s.paired;
  el("pause-panel").hidden = !s.paired;
  if (s.paired) {
    el("paired-text").textContent = `This browser reports to ${s.serverName ?? ""} at ${s.server}.`;
    el("status").textContent =
      s.pausedUntil && new Date(s.pausedUntil) > new Date()
        ? `Paused until ${new Date(s.pausedUntil).toLocaleString()}.`
        : "Protection on.";
  } else {
    el("status").textContent = "Not paired yet.";
  }
}

el<HTMLFormElement>("pair-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  el("pair-error").textContent = "";
  const r = (await ext.runtime.sendMessage({
    type: "pair",
    server: el<HTMLInputElement>("server").value.trim(),
    code: el<HTMLInputElement>("code").value.trim(),
    name: el<HTMLInputElement>("name").value.trim(),
  })) as { ok: boolean; error?: string };
  if (!r.ok) el("pair-error").textContent = r.error ?? "pairing failed";
  await render();
});

el<HTMLFormElement>("unlock-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  el("unlock-error").textContent = "";
  const r = (await ext.runtime.sendMessage({ type: "unlock", code: el<HTMLInputElement>("unlock").value.trim() })) as {
    ok: boolean;
    error?: string;
  };
  if (!r.ok) el("unlock-error").textContent = r.error ?? "that code did not work";
  await render();
});

el<HTMLFormElement>("request-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = (await ext.runtime.sendMessage({
    type: "request_unlock",
    minutes: Number(el<HTMLInputElement>("minutes").value),
    reason: el<HTMLInputElement>("reason").value,
  })) as { ok: boolean; error?: string };
  el("request-note").textContent = r.ok ? "Asked. A partner can send you a code." : (r.error ?? "could not ask");
});

void render();
