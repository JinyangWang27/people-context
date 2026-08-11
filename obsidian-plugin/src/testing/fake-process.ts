/**
 * Test doubles for the narrow spawn port.
 *
 * The fakes exist so a test can assert on the exact argument array and options the bridge
 * would hand to `child_process.spawn` without starting a process. Real-process behaviour —
 * timeouts, cancellation, output limits, a missing executable — is exercised separately
 * against a real `node` child in `bridge.test.ts`.
 */

import type {
  ChildProcessLike,
  ReadableStreamLike,
  SpawnOptionsLike,
  Spawner,
} from "../bridge.js";

type DataListener = (chunk: Buffer | string) => void;

class FakeStream implements ReadableStreamLike {
  private readonly listeners: DataListener[] = [];

  on(event: "data", listener: DataListener): this {
    if (event === "data") {
      this.listeners.push(listener);
    }
    return this;
  }

  emit(chunk: Buffer | string): void {
    for (const listener of [...this.listeners]) {
      listener(chunk);
    }
  }
}

export class FakeChildProcess implements ChildProcessLike {
  readonly pid: number = 424242;
  readonly stdout = new FakeStream();
  readonly stderr = new FakeStream();
  /** Every signal the bridge sent, in order. */
  readonly kills: (NodeJS.Signals | number | undefined)[] = [];

  private readonly errorListeners: ((error: NodeJS.ErrnoException) => void)[] = [];
  private readonly closeListeners: ((code: number | null, signal: string | null) => void)[] = [];

  on(event: "error", listener: (error: NodeJS.ErrnoException) => void): this;
  on(event: "close", listener: (code: number | null, signal: string | null) => void): this;
  on(event: "error" | "close", listener: (...args: never[]) => void): this {
    if (event === "error") {
      this.errorListeners.push(listener as unknown as (error: NodeJS.ErrnoException) => void);
    } else {
      this.closeListeners.push(
        listener as unknown as (code: number | null, signal: string | null) => void,
      );
    }
    return this;
  }

  kill(signal?: NodeJS.Signals | number): boolean {
    this.kills.push(signal);
    return true;
  }

  emitError(error: NodeJS.ErrnoException): void {
    for (const listener of [...this.errorListeners]) {
      listener(error);
    }
  }

  emitClose(code: number | null, signal: string | null = null): void {
    for (const listener of [...this.closeListeners]) {
      listener(code, signal);
    }
  }
}

export interface SpawnCall {
  command: string;
  args: readonly string[];
  options: SpawnOptionsLike;
  child: FakeChildProcess;
}

export interface RecordingSpawner {
  spawn: Spawner;
  calls: SpawnCall[];
  /** The single recorded call, failing loudly when there was not exactly one. */
  only(): SpawnCall;
}

/**
 * A spawner that records every invocation and hands back a controllable child.
 *
 * `respond` runs after the bridge has attached its listeners, which is what lets a test
 * complete the run without any timing assumptions.
 */
export function recordingSpawner(respond?: (child: FakeChildProcess) => void): RecordingSpawner {
  const calls: SpawnCall[] = [];
  const spawn: Spawner = (command, args, options) => {
    const child = new FakeChildProcess();
    calls.push({ command, args, options, child });
    if (respond !== undefined) {
      queueMicrotask(() => {
        respond(child);
      });
    }
    return child;
  };
  return {
    spawn,
    calls,
    only(): SpawnCall {
      if (calls.length !== 1) {
        throw new Error(`expected exactly one spawn call, saw ${calls.length}`);
      }
      return calls[0] as SpawnCall;
    },
  };
}

/** A spawner that completes successfully with the given stdout payload. */
export function succeedingSpawner(stdout: string): RecordingSpawner {
  return recordingSpawner((child) => {
    child.stdout.emit(Buffer.from(stdout, "utf8"));
    child.emitClose(0);
  });
}
