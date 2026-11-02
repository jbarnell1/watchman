// The household server's device API (docs/api-contract.md). fetch is injected so tests use a fake.

import type { ModelsManifest, Pairing, Policy, WatchmanEvent } from "./types";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function failure(res: Response): Promise<ApiError> {
  let code = "error";
  let message = res.statusText;
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string } };
    code = body.error?.code ?? code;
    message = body.error?.message ?? message;
  } catch {
    // not JSON
  }
  return new ApiError(res.status, code, message);
}

export class Api {
  constructor(
    private readonly fetchImpl: FetchLike,
    private readonly server: string,
    private readonly token: string | null,
  ) {}

  private async call(path: string, init: RequestInit = {}): Promise<Response> {
    const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const f = this.fetchImpl; // called unbound: fetch refuses a `this` that is not the global
    const res = await f(this.server.replace(/\/$/, "") + path, { ...init, headers });
    if (!res.ok) throw await failure(res);
    return res;
  }

  private async json<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
    const res = await this.call(path, {
      method: body === undefined ? "GET" : method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return (await res.json()) as T;
  }

  static async pair(
    fetchImpl: FetchLike,
    server: string,
    code: string,
    device: { name: string; platform: string; kind: "extension"; app_version: string },
  ): Promise<Pairing> {
    const api = new Api(fetchImpl, server, null);
    const r = await api.json<{ device_id: string; token: string; person_id: string; server_name: string }>(
      "/api/v1/pair",
      { code, device },
    );
    return { server, token: r.token, device_id: r.device_id, person_id: r.person_id, server_name: r.server_name };
  }

  policyVersion(): Promise<{ policy_version: number; paused_until: string | null }> {
    return this.json("/api/v1/policy/version", undefined, "GET");
  }

  heartbeat(body: Record<string, unknown>): Promise<{ policy_version: number; paused_until: string | null }> {
    return this.json("/api/v1/heartbeat", body);
  }

  policy(): Promise<Policy> {
    return this.json<Policy>("/api/v1/policy");
  }

  events(events: WatchmanEvent[]): Promise<{ accepted: string[]; duplicates: string[]; upload: string[];
    rejected?: { id: string | null; reason: string }[] }> {
    return this.json("/api/v1/events", { events });
  }

  async uploadImage(eventId: string, jpeg: Blob | ArrayBuffer): Promise<void> {
    await this.call(`/api/v1/events/${eventId}/image`, {
      method: "PUT",
      headers: { "Content-Type": "image/jpeg" },
      body: jpeg,
    });
  }

  async judge(jpeg: ArrayBuffer): Promise<{ score: number; verdict: "safe" | "explicit" }> {
    const res = await this.call("/api/v1/judge", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: jpeg });
    return (await res.json()) as { score: number; verdict: "safe" | "explicit" };
  }

  manifest(): Promise<ModelsManifest> {
    return this.json<ModelsManifest>("/api/v1/models/manifest");
  }

  async modelBytes(path: string): Promise<ArrayBuffer> {
    const res = await this.call(path);
    return res.arrayBuffer();
  }

  unlock(code: string): Promise<{ paused_until: string }> {
    return this.json("/api/v1/unlock", { code });
  }

  requestUnlock(minutes: number, reason: string): Promise<unknown> {
    return this.json("/api/v1/unlock/request", { minutes, reason });
  }
}
