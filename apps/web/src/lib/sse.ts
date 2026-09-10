/**
 * Server-sent events over `fetch`.
 *
 * The browser's own `EventSource` cannot issue a POST and cannot send an
 * Authorization header, and both are required here — a chat request carries a
 * body and a bearer token. So the stream is read manually from the response
 * body, which also means a request can be aborted mid-run, which `EventSource`
 * does not support either.
 */

export interface SSEMessage {
  id?: string;
  event: string;
  data: string;
}

/** Parse a byte stream of SSE frames into messages. */
export async function* parseSSE(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SSEMessage> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line. A frame can arrive split across
      // several chunks, so anything after the last separator stays buffered.
      let separator = buffer.indexOf("\n\n");
      while (separator !== -1) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        const message = parseFrame(frame);
        if (message) yield message;
        separator = buffer.indexOf("\n\n");
      }
    }
    // A final frame with no trailing blank line.
    const trailing = parseFrame(buffer);
    if (trailing) yield trailing;
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): SSEMessage | null {
  if (!frame.trim()) return null;

  let id: string | undefined;
  let event = "message";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    // A line starting with ':' is a comment — the server's heartbeat, which
    // exists to stop a proxy reaping the connection while a model thinks.
    if (line.startsWith(":")) continue;
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }

  if (!dataLines.length) return null;
  return { id, event, data: dataLines.join("\n") };
}

export interface StreamOptions {
  method?: "GET" | "POST";
  body?: unknown;
  token?: string | null;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

/** Open an SSE stream against the API. */
export async function* streamRequest(
  url: string,
  options: StreamOptions = {},
): AsyncGenerator<SSEMessage> {
  const { method = "POST", body, token, signal, headers = {} } = options;

  const response = await fetch(url, {
    method,
    signal,
    credentials: "include",
    headers: {
      Accept: "text/event-stream",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    // The error body is JSON (RFC 7807), not an event stream.
    const detail = await response.text().catch(() => "");
    let message = `Request failed (${response.status})`;
    try {
      const problem = JSON.parse(detail);
      message = problem.detail ?? message;
    } catch {
      /* not a problem document; keep the generic message */
    }
    throw new Error(message);
  }
  if (!response.body) throw new Error("The response carried no stream.");

  yield* parseSSE(response.body, signal);
}
