import { describe, expect, it } from "vitest";

import { EventQueue, MAX_BATCH, MAX_QUEUE, MemoryKeyValue } from "../src/shared/queue";
import type { WatchmanEvent } from "../src/shared/types";

function ev(id: string): WatchmanEvent {
  return {
    id, type: "image_covered", occurred_at: "2026-11-01T00:00:00Z", app: "firefox", url: null, title: null,
    action: "covered", scores: { classifier: 0.9 }, region: null, hash: null, has_image: true, detail: {},
  };
}

describe("event queue", () => {
  it("keeps what the server did not take and reports wanted images", async () => {
    const q = new EventQueue(new MemoryKeyValue());
    await q.push(ev("a"));
    await q.push(ev("b"));
    await q.push(ev("c"));
    const wanted = await q.flush(async (batch) => {
      expect(batch.map((e) => e.id)).toEqual(["a", "b", "c"]);
      return { accepted: ["a"], duplicates: ["b"], upload: ["a"] };
    });
    expect(wanted).toEqual(["a"]);
    expect((await q.all()).map((e) => e.id)).toEqual(["c"]);
  });

  it("sends at most one batch per flush and survives a failed send", async () => {
    const q = new EventQueue(new MemoryKeyValue());
    for (let i = 0; i < MAX_BATCH + 5; i++) await q.push(ev(String(i)));
    await expect(q.flush(async () => { throw new Error("offline"); })).rejects.toThrow("offline");
    expect(await q.size()).toBe(MAX_BATCH + 5);
    await q.flush(async (batch) => {
      expect(batch.length).toBe(MAX_BATCH);
      return { accepted: batch.map((e) => e.id), duplicates: [], upload: [] };
    });
    expect(await q.size()).toBe(5);
  });

  it("caps the queue so an offline week cannot fill storage", async () => {
    const q = new EventQueue(new MemoryKeyValue());
    for (let i = 0; i < MAX_QUEUE + 10; i++) await q.push(ev(String(i)));
    expect(await q.size()).toBe(MAX_QUEUE);
    expect((await q.all())[0].id).toBe("10");
  });
});
