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

const TIMEOUT_MS = 30_000;

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
