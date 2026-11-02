// Events wait here until the server takes them. Storage is an interface so tests use memory.

import type { WatchmanEvent } from "./types";

export interface KeyValue {
  get(key: string): Promise<unknown>;
  set(key: string, value: unknown): Promise<void>;
}

export class MemoryKeyValue implements KeyValue {
  private data = new Map<string, unknown>();
  async get(key: string): Promise<unknown> {
    return this.data.get(key);
  }
  async set(key: string, value: unknown): Promise<void> {
    this.data.set(key, value);
  }
}

const KEY = "event_queue";
export const MAX_BATCH = 200;
export const MAX_QUEUE = 5000;

export class EventQueue {
  constructor(private readonly store: KeyValue) {}

  async all(): Promise<WatchmanEvent[]> {
    return ((await this.store.get(KEY)) as WatchmanEvent[] | undefined) ?? [];
  }

  async push(event: WatchmanEvent): Promise<void> {
    const q = await this.all();
    q.push(event);
    if (q.length > MAX_QUEUE) q.splice(0, q.length - MAX_QUEUE);
    await this.store.set(KEY, q);
  }

  /** Sends up to MAX_BATCH per call, drops what the server accepted or already had, keeps the rest
   * for the next try. Returns the ids the server wants images for. */
  async flush(send: (batch: WatchmanEvent[]) => Promise<{ accepted: string[]; duplicates: string[]; upload: string[];
    rejected?: { id: string | null; reason: string }[] }>): Promise<string[]> {
    const q = await this.all();
    if (q.length === 0) return [];
    const batch = q.slice(0, MAX_BATCH);
    const result = await send(batch);
    // Rejected events are malformed; sending them again would fail again.
    const rejected = (result.rejected ?? []).map((r) => r.id).filter((id): id is string => Boolean(id));
    const done = new Set([...result.accepted, ...result.duplicates, ...rejected]);
    await this.store.set(
      KEY,
      q.filter((e) => !done.has(e.id)),
    );
    return result.upload;
  }

  async size(): Promise<number> {
    return (await this.all()).length;
  }
}
