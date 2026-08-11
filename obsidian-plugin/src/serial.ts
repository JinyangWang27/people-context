/**
 * A one-at-a-time task queue.
 *
 * Settings persistence needs this because the host calls a text field's `onChange` handler
 * per keystroke without awaiting the previous call. Several saves then overlap, and if an
 * earlier write lands after a later one the file on disk keeps the older value while memory
 * holds the newer — a divergence that only becomes visible after a reload. Chaining the writes
 * makes the last submitted value the last one written.
 */
export class SerialQueue {
  /** Always settled, never rejected: a failed task must not stall the queue behind it. */
  private tail: Promise<void> = Promise.resolve();

  /** Run `task` after every task submitted before it, and resolve with its outcome. */
  run(task: () => Promise<void>): Promise<void> {
    const next = this.tail.then(task);
    this.tail = next.then(
      () => undefined,
      () => undefined,
    );
    return next;
  }

  /** Resolve once everything submitted so far has finished. */
  drain(): Promise<void> {
    return this.tail;
  }
}
