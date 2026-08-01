// Document channel to the LibreOffice sidebar shell (sidebar bead contract,
// src/coffice/sidebar/contract.py). The MVP shell is a native AWT panel (no
// WebView), so this bridge is dormant when no host posts back: commands time
// out and resolve to undefined, and the app keeps working standalone.

const TYPE_COMMAND = "coffice.command";
const TYPE_RESULT = "coffice.result";
const TYPE_HANDSHAKE = "coffice.handshake";
const TYPE_PING = "coffice.ping";
const TYPE_PONG = "coffice.pong";

export interface ShellResult {
  ok: boolean;
  result?: Record<string, unknown> | null;
  error?: string | null;
}

export interface ShellHandshake {
  version: number;
  url: string;
  docId: string | null;
}

function randomId(): string {
  return `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function postToHost(message: Record<string, unknown>): void {
  if (typeof window === "undefined" || !window.parent || window.parent === window) {
    return;
  }
  window.parent.postMessage(message, "*");
}

/**
 * Send a document command (openDoc / focus / save) to the shell and await the
 * matching result. Resolves undefined when no host answers (standalone mode).
 */
export function sendShellCommand(
  command: string,
  args: Record<string, unknown> = {},
  timeoutMs = 1500,
): Promise<ShellResult | undefined> {
  return new Promise((resolve) => {
    if (typeof window === "undefined" || window.parent === window) {
      resolve(undefined);
      return;
    }
    const id = randomId();
    let settled = false;
    const onMessage = (event: MessageEvent): void => {
      const data = event.data;
      if (
        data &&
        data.type === TYPE_RESULT &&
        data.id === id &&
        typeof data.ok === "boolean"
      ) {
        settled = true;
        cleanup();
        resolve({ ok: data.ok, result: data.result ?? null, error: data.error ?? null });
      }
    };
    const timer = window.setTimeout(() => {
      if (!settled) {
        cleanup();
        resolve(undefined);
      }
    }, timeoutMs);
    const cleanup = (): void => {
      window.removeEventListener("message", onMessage);
      window.clearTimeout(timer);
    };
    window.addEventListener("message", onMessage);
    postToHost({ type: TYPE_COMMAND, id, command, args });
  });
}

/** Subscribe to shell handshakes; returns an unsubscribe function. */
export function onShellHandshake(
  callback: (handshake: ShellHandshake) => void,
): () => void {
  const handler = (event: MessageEvent): void => {
    const data = event.data;
    if (
      data &&
      data.type === TYPE_HANDSHAKE &&
      typeof data.version === "number"
    ) {
      callback({
        version: data.version,
        url: String(data.url ?? ""),
        docId: data.docId ?? null,
      });
    }
  };
  window.addEventListener("message", handler);
  return () => window.removeEventListener("message", handler);
}

/** Ping the host (the shell answers coffice.pong with its protocol version). */
export function pingShell(): void {
  postToHost({ type: TYPE_PING });
}

export { TYPE_COMMAND, TYPE_PONG, TYPE_RESULT };
