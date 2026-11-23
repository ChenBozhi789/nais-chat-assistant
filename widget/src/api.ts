/** Calls the backend's POST /chat. The API key never reaches the browser. */

export interface Source {
  title: string;
  snippet: string;
  url?: string;
}

export interface ChatReply {
  answer: string;
  sources: Source[];
}

export interface HistoryTurn {
  role: "user" | "assistant";
  content: string;
}

// The MCP agent can take several sequential tool rounds (list + read documents),
// so allow longer than a single model call. Must exceed the backend's deadline.
const TIMEOUT_MS = 75_000;

/** An error whose message is safe and friendly to show to visitors. */
export class ChatApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ChatApiError";
  }
}

function friendlyStatusMessage(status: number, detail: string | undefined): string {
  if (status === 422) {
    return "That message couldn't be sent. Please keep it under 1,000 characters.";
  }
  if (status === 429) {
    return "Lots of people are asking right now. Please wait a moment and try again.";
  }
  if (status === 503 && detail) {
    return detail;
  }
  return "The assistant is having trouble right now. Please try again.";
}

/** Read FastAPI's `{"detail": "..."}` when present; ignore anything else. */
async function readDetail(response: Response): Promise<string | undefined> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      const { detail } = body as { detail: unknown };
      return typeof detail === "string" ? detail : undefined;
    }
  } catch {
    // Not JSON; fall through.
  }
  return undefined;
}

function parseReply(body: unknown): ChatReply {
  const data = body as { answer?: unknown; sources?: unknown } | null;
  if (!data || typeof data.answer !== "string") {
    throw new ChatApiError("The assistant sent an unexpected reply. Please try again.");
  }
  const sources = Array.isArray(data.sources) ? data.sources : [];
  return {
    answer: data.answer,
    sources: sources.flatMap((item: unknown): Source[] => {
      const s = item as Partial<Source> | null;
      if (!s || typeof s.title !== "string") return [];
      return [
        {
          title: s.title,
          snippet: typeof s.snippet === "string" ? s.snippet : "",
          url: typeof s.url === "string" ? s.url : undefined,
        },
      ];
    }),
  };
}

export async function sendChat(
  apiBase: string,
  message: string,
  history: HistoryTurn[],
  signal?: AbortSignal,
): Promise<ChatReply> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener("abort", forwardAbort);

  try {
    const response = await fetch(`${apiBase}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new ChatApiError(
        friendlyStatusMessage(response.status, await readDetail(response)),
        response.status,
      );
    }
    return parseReply(await response.json());
  } catch (error) {
    if (error instanceof ChatApiError) throw error;
    if (signal?.aborted) throw error; // the caller cancelled; not a failure to show
    if (controller.signal.aborted) {
      throw new ChatApiError("The assistant took too long to answer. Please try again.");
    }
    throw new ChatApiError(
      "Couldn't reach the assistant. Please check your connection and try again.",
    );
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }
}

/** Pull complete `data: {...}` events out of an SSE buffer; returns the unread rest. */
function takeEvents(buffer: string): { events: unknown[]; rest: string } {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const events = parts.flatMap((part): unknown[] => {
    const data = part
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return [];
    try {
      return [JSON.parse(data)];
    } catch {
      return [];
    }
  });
  return { events, rest };
}

/**
 * Like `sendChat`, but reads POST /chat/stream and calls `onDelta` with each text
 * fragment as it arrives and `onStage` when the backend reports progress
 * (e.g. "searching"). Resolves with the final reply (answer + sources).
 * Falls back to plain `sendChat` when the backend has no streaming route.
 */
export async function sendChatStream(
  apiBase: string,
  message: string,
  history: HistoryTurn[],
  onDelta: (text: string) => void,
  onStage: (stage: string) => void,
  signal?: AbortSignal,
): Promise<ChatReply> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener("abort", forwardAbort);

  try {
    const response = await fetch(`${apiBase}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history }),
      signal: controller.signal,
    });
    if (response.status === 404 || response.status === 405) {
      return await sendChat(apiBase, message, history, signal);
    }
    if (!response.ok || !response.body) {
      throw new ChatApiError(
        friendlyStatusMessage(response.status, await readDetail(response)),
        response.status,
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let reply: ChatReply | null = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      const parsed = takeEvents(buffer);
      buffer = parsed.rest;
      for (const raw of parsed.events) {
        const event = raw as {
          type?: unknown;
          text?: unknown;
          message?: unknown;
          stage?: unknown;
        };
        if (event.type === "delta" && typeof event.text === "string") {
          onDelta(event.text);
        } else if (event.type === "status" && typeof event.stage === "string") {
          onStage(event.stage);
        } else if (event.type === "done") {
          reply = parseReply(raw);
        } else if (event.type === "error") {
          throw new ChatApiError(
            typeof event.message === "string"
              ? event.message
              : "The assistant is having trouble right now. Please try again.",
          );
        }
      }
    }
    if (!reply) {
      throw new ChatApiError("The assistant's reply was cut off. Please try again.");
    }
    return reply;
  } catch (error) {
    if (error instanceof ChatApiError) throw error;
    if (signal?.aborted) throw error; // the caller cancelled; not a failure to show
    if (controller.signal.aborted) {
      throw new ChatApiError("The assistant took too long to answer. Please try again.");
    }
    throw new ChatApiError(
      "Couldn't reach the assistant. Please check your connection and try again.",
    );
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }
}

/** Fetch mp3 audio for `text` from POST /tts. Rejects if voice is unavailable. */
export async function fetchSpeech(
  apiBase: string,
  text: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const response = await fetch(`${apiBase}/tts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!response.ok) throw new Error(`tts ${response.status}`);
  return response.blob();
}

/** Send a 16 kHz mono WAV clip to POST /stt and return the transcript ("" if none). */
export async function transcribeAudio(
  apiBase: string,
  wav: Blob,
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetch(`${apiBase}/stt`, {
    method: "POST",
    headers: { "Content-Type": "audio/wav" },
    body: wav,
    signal,
  });
  if (!response.ok) throw new Error(`stt ${response.status}`);
  const data = (await response.json()) as { text?: unknown };
  return typeof data.text === "string" ? data.text : "";
}
