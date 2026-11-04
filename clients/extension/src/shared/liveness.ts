// Judge liveness for the heartbeat: how many images a model actually scored, and how long the
// browser was in front of an active user, since the last heartbeat the server took. A browser that
// is in use but judges nothing is a browser whose protection has stopped working. Pure: the
// background supplies the clock and the idle and focus answers.

export interface JudgeLiveness {
  window_s: number;
  screen_on_s: number;
  judged: number;
  last_judged_at: string | null;
}

export interface LivenessState {
  /** Start of the window (ms since epoch): the last delivered heartbeat. */
  since: number;
  screen_on_ms: number;
  judged: number;
  last_judged_at: string | null;
  /** When the screen was last sampled (ms since epoch), or null before the first sample. */
  sampled_at: number | null;
}

/** Longest gap between two samples that still counts as screen time (the flush alarm is 30 s). */
export const MAX_SAMPLE_GAP_MS = 90_000;

export function freshLiveness(now: number, lastJudgedAt: string | null = null): LivenessState {
  return { since: now, screen_on_ms: 0, judged: 0, last_judged_at: lastJudgedAt, sampled_at: null };
}

/** Fold in images scored since the last fold. */
export function addJudged(s: LivenessState, n: number, lastAt: string | null): LivenessState {
  const last = lastAt && (!s.last_judged_at || lastAt > s.last_judged_at) ? lastAt : s.last_judged_at;
  return { ...s, judged: s.judged + n, last_judged_at: last };
}

/** One sample: the time since the previous sample counts when the user is active now. Approximate. */
export function addScreenSample(s: LivenessState, now: number, active: boolean): LivenessState {
  const gap = s.sampled_at === null ? 0 : Math.max(0, Math.min(now - s.sampled_at, MAX_SAMPLE_GAP_MS));
  return { ...s, screen_on_ms: s.screen_on_ms + (active ? gap : 0), sampled_at: now };
}

export function livenessBody(s: LivenessState, now: number): JudgeLiveness {
  const window_s = Math.max(0, Math.round((now - s.since) / 1000));
  return {
    window_s,
    screen_on_s: Math.min(window_s, Math.round(s.screen_on_ms / 1000)),
    judged: s.judged,
    last_judged_at: s.last_judged_at,
  };
}

/** After the server took a heartbeat built at `sentAt`: a new window from then. Whatever was folded
 * in while the request was in flight stays for the next one. */
export function delivered(s: LivenessState, sent: JudgeLiveness, sentAt: number): LivenessState {
  return {
    since: sentAt,
    screen_on_ms: Math.max(0, s.screen_on_ms - sent.screen_on_s * 1000),
    judged: Math.max(0, s.judged - sent.judged),
    last_judged_at: s.last_judged_at,
    sampled_at: s.sampled_at,
  };
}
