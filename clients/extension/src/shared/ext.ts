// One name for the extension API on every browser. Firefox exposes `browser` with promises;
// Chrome's `chrome` returns promises in MV3.

export const ext: typeof chrome = (globalThis as unknown as { browser?: typeof chrome }).browser ?? chrome;

export class StorageKeyValue {
  async get(key: string): Promise<unknown> {
    const r = await ext.storage.local.get(key);
    return r[key];
  }
  async set(key: string, value: unknown): Promise<void> {
    await ext.storage.local.set({ [key]: value });
  }
  async remove(key: string): Promise<void> {
    await ext.storage.local.remove(key);
  }
}

export function uuid(): string {
  return crypto.randomUUID();
}

export function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function platformName(): "chrome" | "edge" | "firefox" | "firefox-android" | "safari" {
  const ua = navigator.userAgent;
  if (/Firefox/.test(ua)) return /Android/.test(ua) ? "firefox-android" : "firefox";
  if (/Edg\//.test(ua)) return "edge";
  if (/Safari/.test(ua) && !/Chrome/.test(ua)) return "safari";
  return "chrome";
}
