import { describe, expect, it } from "vitest";

import { SerialQueue } from "./serial.js";

/** A promise plus the handle that settles it, so a test controls completion order exactly. */
function deferred(): { promise: Promise<void>; resolve: () => void; reject: (e: Error) => void } {
  let resolve!: () => void;
  let reject!: (e: Error) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("SerialQueue", () => {
  it("runs tasks in submission order even when an earlier one is slower", async () => {
    const queue = new SerialQueue();
    const order: string[] = [];
    const first = deferred();

    // This is the shape the settings tab produces: a second keystroke arrives while the write
    // for the first is still in flight.
    const a = queue.run(async () => {
      await first.promise;
      order.push("a");
    });
    const b = queue.run(async () => {
      order.push("b");
    });

    expect(order).toEqual([]);
    first.resolve();
    await Promise.all([a, b]);

    // Without the queue, "b" would have been written first and "a" would have overwritten it
    // with the older value.
    expect(order).toEqual(["a", "b"]);
  });

  it("writes the last submitted value last", async () => {
    const queue = new SerialQueue();
    const written: string[] = [];
    const gate = deferred();

    const slow = queue.run(async () => {
      await gate.promise;
      written.push("/tmp/first.db");
    });
    const fast = queue.run(async () => {
      written.push("/tmp/second.db");
    });

    gate.resolve();
    await Promise.all([slow, fast]);

    expect(written.at(-1)).toBe("/tmp/second.db");
  });

  it("reports a task's failure to its own caller", async () => {
    const queue = new SerialQueue();

    await expect(
      queue.run(async () => {
        throw new Error("disk full");
      }),
    ).rejects.toThrowError("disk full");
  });

  it("keeps running after a task fails, rather than stalling the queue", async () => {
    const queue = new SerialQueue();
    const order: string[] = [];

    const failing = queue.run(async () => {
      throw new Error("disk full");
    });
    const following = queue.run(async () => {
      order.push("later write");
    });

    await expect(failing).rejects.toThrowError("disk full");
    await following;

    expect(order).toEqual(["later write"]);
  });

  it("drains once everything submitted so far has settled", async () => {
    const queue = new SerialQueue();
    const order: string[] = [];

    void queue.run(async () => {
      order.push("one");
    });
    void queue.run(async () => {
      order.push("two");
    });

    await queue.drain();

    expect(order).toEqual(["one", "two"]);
  });
});
