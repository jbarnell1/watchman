// A fake `chrome` so modules that touch the extension API can load under Node.
import { vi } from "vitest";

const store = new Map<string, unknown>();

export const fakeChrome = {
  runtime: {
    sendMessage: vi.fn(async () => ({})),
    onMessage: { addListener: vi.fn() },
    onInstalled: { addListener: vi.fn() },
    onStartup: { addListener: vi.fn() },
    getURL: (p: string) => `chrome-extension://test/${p}`,
    openOptionsPage: vi.fn(),
  },
  storage: {
    local: {
      get: vi.fn(async (key: string) => ({ [key]: store.get(key) })),
      set: vi.fn(async (obj: Record<string, unknown>) => {
        for (const [k, v] of Object.entries(obj)) store.set(k, v);
      }),
      remove: vi.fn(async (key: string) => {
        store.delete(key);
      }),
    },
  },
  alarms: {
    create: vi.fn(async () => undefined),
    onAlarm: { addListener: vi.fn() },
  },
};

(globalThis as unknown as { chrome: unknown }).chrome = fakeChrome;
(globalThis as unknown as { __WATCHMAN_TEST__: boolean }).__WATCHMAN_TEST__ = true;

export function resetStore(): void {
  store.clear();
}
