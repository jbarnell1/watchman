// Types shared by the background, the scoring engine and the content script.

export type Decision = "safe" | "flag" | "cover";

export interface Thresholds {
  cover: number;
  flag: number;
  detector_cover: number;
  ask?: number;
}

export interface Policy {
  version: number;
  thresholds: Thresholds;
  strict_mode: boolean;
  // Posture (ADR-022): report only when cover_on_device is false; no ask band when ask_server is false.
  cover_on_device?: boolean;
  ask_server?: boolean;
  coverage?: string;
  explicit_labels?: string[];
  risky_apps: string[];
  blocked_apps: string[];
  protected_browser: string;
  sample_interval_s: number;
  whole_screen_interval_s: number;
  paused_until: string | null;
  known_bad_hashes: string[];
  models_manifest_url: string;
}

export interface ModelEntry {
  role: string;
  file: string;
  url: string;
  sha256: string;
  bytes: number;
  input: {
    name: string;
    size: number;
    layout: "NCHW" | "NHWC";
    color: "RGB" | "BGR";
    scale: number;
    mean: number[];
    std: number[];
    resize: string;
  };
  output: { name: string; kind: "softmax" | "sigmoid" | "yolov8"; labels: string[]; positive?: string };
}

export interface ModelsManifest {
  version: number;
  models: ModelEntry[];
}

export interface Pairing {
  server: string; // https://watchman.tailnet.ts.net
  token: string;
  device_id: string;
  person_id: string;
  server_name: string;
}

export interface WatchmanEvent {
  id: string;
  type: "image_covered" | "image_flagged" | "protection_changed" | "daily_stats";
  occurred_at: string;
  app: string | null;
  url: string | null;
  title: string | null;
  action: "covered" | "flagged" | "none";
  scores: { classifier: number } | null;
  region: { w: number; h: number; screen_w: number; screen_h: number } | null;
  hash: string | null;
  has_image: boolean;
  detail: Record<string, unknown>;
}

// Popup to background: score every image on the active tab (numbers only, nothing stored).
export interface PageImage {
  url: string;
  width: number;
  height: number;
}

export interface PageScore extends PageImage {
  hash: string | null;
  score: number | null;
  decision: Decision | "error" | "tiny";
}

// Content script to background.
export interface JudgeRequest {
  type: "judge";
  url: string;
  width: number; // natural size
  height: number;
  displayWidth: number;
  displayHeight: number;
  pageUrl: string;
  pageTitle: string;
}

// Content script to background: one sampled video frame (ADR-023). The crop travels as JPEG.
export interface FrameJudgeRequest {
  type: "judge_frame";
  jpegBase64: string;
  hash: string;
  source: string;
  width: number;
  height: number;
  displayWidth: number;
  displayHeight: number;
  pageUrl: string;
  pageTitle: string;
}

// Content script to background: one sampled video frame (ADR-023). The crop travels as JPEG.
export interface FrameJudgeRequest {
  type: "judge_frame";
  jpegBase64: string;
  hash: string;
  source: string;
  width: number;
  height: number;
  displayWidth: number;
  displayHeight: number;
  pageUrl: string;
  pageTitle: string;
}

export interface JudgeResponse {
  decision: Decision;
  hash: string | null;
  score: number | null;
  reason: "unpaired" | "paused" | "tiny" | "known_bad" | "cached" | "scored" | "asking" | "error";
}

// Background to the scoring engine (offscreen document on Chrome, in-page on Firefox).
export interface ScoreRequest {
  type: "score";
  url: string;
  maxSide: number; // longest side of the JPEG kept for upload
  wantImage: boolean;
}

// Background to the engine: score pixels the content script already has (a video frame).
export interface ScoreBytesRequest {
  type: "score_bytes";
  jpegBase64: string;
}

// Background to the engine: score pixels the content script already has (a video frame).
export interface ScoreBytesRequest {
  type: "score_bytes";
  jpegBase64: string;
}

export interface ScoreResult {
  ok: true;
  hash: string;
  score: number;
  width: number;
  height: number;
  jpegBase64: string | null;
  /** Milliseconds the engine spent, and whether its hash cache answered (ADR-025). */
  ms?: number;
  cached?: boolean;
}

export interface ScoreFailure {
  ok: false;
  error: string;
}
