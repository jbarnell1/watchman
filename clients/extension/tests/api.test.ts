import { describe, expect, it, vi } from "vitest";

import { Api, ApiError } from "../src/shared/api";

function fakeFetch(handler: (url: string, init?: RequestInit) => { status: number; body: unknown }) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const r = handler(url, init);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { "Content-Type": "application/json" } });
  });
}

describe("api", () => {
  it("pairs and keeps the token for later calls", async () => {
    const f = fakeFetch((url, init) => {
      if (url.endsWith("/api/v1/pair")) {
        const body = JSON.parse(String(init?.body));
        expect(body.code).toBe("K7Q-4MZ-P2D");
        expect(body.device.kind).toBe("extension");
        return { status: 200, body: { device_id: "d", token: "tok", person_id: "p", server_name: "Example household" } };
      }
      if (url.endsWith("/api/v1/policy")) {
        expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer tok");
        return { status: 200, body: { version: 3, thresholds: { cover: 0.8, flag: 0.6, detector_cover: 0.55 } } };
      }
      return { status: 404, body: { error: { code: "not_found", message: "no" } } };
    });
    const pairing = await Api.pair(f, "https://watchman.test/", "K7Q-4MZ-P2D", {
      name: "n", platform: "firefox", kind: "extension", app_version: "0.1.0",
    });
    expect(pairing.token).toBe("tok");
    const api = new Api(f, pairing.server, pairing.token);
    expect((await api.policy()).version).toBe(3);
    expect(f.mock.calls[1][0]).toBe("https://watchman.test/api/v1/policy");
  });

  it("surfaces the server's error code", async () => {
    const f = fakeFetch(() => ({ status: 400, body: { error: { code: "unlock_code_invalid", message: "nope" } } }));
    const api = new Api(f, "https://w", "t");
    await expect(api.unlock("000-000")).rejects.toMatchObject({ status: 400, code: "unlock_code_invalid" } as Partial<ApiError>);
  });

  it("asks the server to judge a crop", async () => {
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe("https://w/api/v1/judge");
      expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("image/jpeg");
      return new Response(JSON.stringify({ score: 0.1, verdict: "safe" }), { status: 200 });
    });
    expect((await new Api(f, "https://w", "t").judge(new ArrayBuffer(3))).verdict).toBe("safe");
  });

  it("uploads images as JPEG bodies", async () => {
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe("https://w/api/v1/events/abc/image");
      expect(init?.method).toBe("PUT");
      expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("image/jpeg");
      return new Response(null, { status: 204 });
    });
    await new Api(f, "https://w", "t").uploadImage("abc", new ArrayBuffer(3));
    expect(f).toHaveBeenCalledOnce();
  });
});
