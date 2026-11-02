// The engine page: Chrome opens it as an offscreen document, Firefox as an iframe inside the
// background page. It answers the background's messages. Nothing else talks to it.

import { Api } from "../shared/api";
import { ext } from "../shared/ext";
import type { ModelEntry, ScoreBytesRequest, ScoreRequest } from "../shared/types";
import { Engine } from "./engine";
import { ensureModel } from "./models";
import { OnnxScorer } from "./scorer";

interface LoadRequest {
  target: "engine";
  type: "load";
  server: string;
  token: string;
  entry: ModelEntry;
}

type EngineMessage =
  | (ScoreRequest & { target: "engine" })
  | (ScoreBytesRequest & { target: "engine" })
  | LoadRequest
  | { target: "engine"; type: "ping" };

let engine: Engine | null = null;
let loading: Promise<void> | null = null;

async function load(msg: LoadRequest): Promise<void> {
  const api = new Api(fetch, msg.server, msg.token);
  const bytes = await ensureModel(msg.entry.file, msg.entry.sha256, () => api.modelBytes(msg.entry.url));
  const scorer = await OnnxScorer.create(bytes, msg.entry, ext.runtime.getURL("ort/"));
  engine = new Engine(scorer);
}

ext.runtime.onMessage.addListener((raw: unknown, _sender, sendResponse) => {
  const msg = raw as EngineMessage;
  if (!msg || msg.target !== "engine") return false;
  (async () => {
    if (msg.type === "ping") return { ok: true, ready: engine !== null };
    if (msg.type === "load") {
      loading = loading ?? load(msg);
      try {
        await loading;
        return { ok: true };
      } catch (err) {
        loading = null;
        return { ok: false, error: err instanceof Error ? err.message : String(err) };
      }
    }
    if (msg.type === "score") {
      if (!engine) return { ok: false, error: "model not loaded" };
      return engine.score(msg.url, msg.maxSide, msg.wantImage);
    }
    if (msg.type === "score_bytes") {
      if (!engine) return { ok: false, error: "model not loaded" };
      return engine.scoreBytes(msg.jpegBase64);
    }
    return { ok: false, error: "unknown message" };
  })().then(sendResponse);
  return true;
});
