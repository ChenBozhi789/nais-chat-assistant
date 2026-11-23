import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { ChatApiError, fetchSpeech, sendChatStream, type HistoryTurn, type Source } from "./api";
import type { WidgetConfig } from "./config";
import { FormattedText } from "./FormattedText";
import { useSpeechInput } from "./useSpeechInput";

/** Language the browser should listen for (New Zealand English). */
const MAX_MESSAGE_CHARS = 1000;
const MAX_HISTORY_TURNS = 6;
const MAX_HISTORY_CHARS = 2000;

const WELCOME =
  "Kia ora! My name is Jack, I am here to help you with your questions about the Nelson AI Sandbox.";

// `label` is the short text on the button; `question` is what actually gets sent.
const STARTERS = [
  { label: "Where is the Sandbox and when is it open?", question: "Where is the Sandbox and when is it open?" },
  { label: "Upcoming workshops", question: "What workshops are coming up?" },
  { label: "Free AI tools", question: "What free AI tools can I use?" },
];

const AUTO_OPEN_DELAY_MS = 300;
const AUTO_OPEN_KEY = "nais-assistant-greeted";

const PRIVACY_NOTICE =
  "AI can make mistakes. Please check important details with NAIS.";

interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
}

const THINKING_LABEL = "Thinking";
// Progress labels for the stages the backend reports while the visitor waits.
const STAGE_LABELS: Record<string, string> = {
  searching: "Searching NAIS information",
};

// "loading": waiting for the first text; "streaming": the reply is being written.
type Status = "idle" | "loading" | "streaming" | "error";

/** Only the last few turns are sent, and only from memory (nothing is stored). */
function toHistory(messages: Message[]): HistoryTurn[] {
  return messages.slice(-MAX_HISTORY_TURNS).map(({ role, content }) => ({
    role,
    content: content.slice(0, MAX_HISTORY_CHARS),
  }));
}

const MAX_SPOKEN_CHARS = 1400;

/** Plain text for the voice: drop markdown symbols and keep it under the /tts limit. */
function toSpeakable(text: string): string {
  const plain = text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/[*_#`>]/g, "")
    .replace(/^\s*[-•]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  if (plain.length <= MAX_SPOKEN_CHARS) return plain;
  const cut = plain.slice(0, MAX_SPOKEN_CHARS);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("? "), cut.lastIndexOf("! "));
  return end > 0 ? cut.slice(0, end + 1) : cut;
}

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M4 3h16a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H9l-5 4v-4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"
      />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        d="M6 6l12 12M18 6L6 18"
      />
    </svg>
  );
}

function SpeakerIcon({ active }: { active: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <path fill="currentColor" d="M3 9v6h4l5 4V5L7 9H3z" />
      {active ? (
        <path
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12"
        />
      ) : (
        <path
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          d="M16 9l5 6M21 9l-5 6"
        />
      )}
    </svg>
  );
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V22h2v-3.08A7 7 0 0 0 19 12h-2z"
      />
    </svg>
  );
}

function SourceList({ sources }: { sources: Source[] }) {
  return (
    <details className="sources">
      <summary>Sources ({sources.length})</summary>
      <ul>
        {sources.map((source, index) => (
          <li key={source.url ?? `${source.title}-${index}`}>
            {source.url ? (
              <a href={source.url} target="_blank" rel="noopener noreferrer">
                {source.title}
              </a>
            ) : (
              <span>{source.title}</span>
            )}
            {source.snippet && <p className="snippet">{source.snippet}</p>}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function ChatWidget({ config }: { config: WidgetConfig }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [errorText, setErrorText] = useState("");
  const [loadingLabel, setLoadingLabel] = useState(THINKING_LABEL);

  const nextId = useRef(1);
  const failed = useRef<{ question: string; prior: Message[] } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const scrolledTo = useRef(0); // id of the answer last scrolled into view
  const bubbleRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Grow the textarea with its content (CSS max-height then makes it scroll).
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    // border-box height: scrollHeight excludes the borders, so add them back.
    el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`;
  }, [input, open]);
  const listRef = useRef<HTMLDivElement>(null);

  const audio = useRef<HTMLAudioElement | null>(null);
  const speechAbort = useRef<AbortController | null>(null);
  const [, setSpeaking] = useState(false);
  const [needsTap, setNeedsTap] = useState(false);
  const [hintShown, setHintShown] = useState(false);
  // Replies are read aloud while voice is on; the speaker button turns it off.
  const [voiceOn, setVoiceOn] = useState(true);
  const voiceOnRef = useRef(true);

  const stopSpeech = useCallback(() => {
    speechAbort.current?.abort();
    audio.current?.pause();
    audio.current = null;
    setSpeaking(false);
  }, []);

  /** Read text aloud. If the browser blocks autoplay, wait for the first user gesture. */
  const speak = useCallback(
    async (text: string) => {
      stopSpeech();
      const controller = new AbortController();
      speechAbort.current = controller;
      try {
        const blob = await fetchSpeech(config.apiBase, text, controller.signal);
        if (controller.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        const player = new Audio(url);
        audio.current = player;
        const done = () => {
          URL.revokeObjectURL(url);
          setSpeaking(false);
        };
        player.onended = done;
        player.onerror = done;
        try {
          await player.play();
          setSpeaking(true);
          setNeedsTap(false);
        } catch (error) {
          if (!(error instanceof DOMException) || error.name !== "NotAllowedError") {
            done();
            return;
          }
          // Autoplay blocked: play on the visitor's first click or key press.
          setNeedsTap(true);
          setHintShown(true);
          const resume = () => {
            document.removeEventListener("pointerdown", resume, true);
            document.removeEventListener("keydown", resume, true);
            if (audio.current !== player) return;
            setNeedsTap(false);
            player.play().then(() => setSpeaking(true), done);
          };
          document.addEventListener("pointerdown", resume, true);
          document.addEventListener("keydown", resume, true);
        }
      } catch {
        // Voice is optional: if it fails the text greeting is still shown.
        if (!controller.signal.aborted) setSpeaking(false);
      }
    },
    [config.apiBase, stopSpeech],
  );

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");

  /** Off: stop and stay silent. On: read the latest assistant message (or the greeting). */
  const toggleVoice = () => {
    const next = !voiceOn;
    voiceOnRef.current = next;
    setVoiceOn(next);
    setNeedsTap(false);
    if (!next) stopSpeech();
    else void speak(lastAssistant ? toSpeakable(lastAssistant.content) : WELCOME);
  };

  const mic = useSpeechInput(config.apiBase, (text) => setInput(text.slice(0, MAX_MESSAGE_CHARS)));

  const toggleMic = () => {
    if (mic.listening) {
      mic.stop();
      return;
    }
    // Silence the assistant so it doesn't hear itself.
    stopSpeech();
    setNeedsTap(false);
    setInput("");
    mic.start();
  };

  const close = useCallback(() => {
    stopSpeech();
    setOpen(false);
    bubbleRef.current?.focus();
  }, [stopSpeech]);

  // Once per browser session: open the window shortly after load and read the greeting.
  useEffect(() => {
    if (config.autoOpen === "off") return;
    try {
      if (config.autoOpen === "session" && sessionStorage.getItem(AUTO_OPEN_KEY)) return;
    } catch {
      // Storage unavailable: still greet.
    }
    const timer = window.setTimeout(() => {
      try {
        sessionStorage.setItem(AUTO_OPEN_KEY, "1");
      } catch {
        // ignore
      }
      setOpen(true);
      void speak(WELCOME);
    }, AUTO_OPEN_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [config.autoOpen, speak]);

  // Stop any audio when the widget unmounts.
  useEffect(() => stopSpeech, [stopSpeech]);

  // Escape closes the window, wherever focus currently is.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  // Move focus into the window when it opens.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Keep the newest message in view. A new answer is scrolled to its first
  // line (not its bottom) so the reader starts at the top of the reply.
  useEffect(() => {
    const list = listRef.current;
    if (!list) {
      scrolledTo.current = 0; // window closed: scroll again when it reopens
      return;
    }
    const last = messages[messages.length - 1];
    const answer =
      (status === "idle" || status === "streaming") && last?.role === "assistant"
        ? list.querySelector<HTMLElement>(`[data-message-id="${last.id}"]`)
        : null;
    if (answer) {
      // Scroll once per answer, so the text growing while it streams in does
      // not fight the reader's own scrolling.
      if (scrolledTo.current === last.id) return;
      scrolledTo.current = last.id;
      list.scrollTop +=
        answer.getBoundingClientRect().top - list.getBoundingClientRect().top - 8;
    } else {
      list.scrollTop = list.scrollHeight;
    }
  }, [messages, status, open]);

  // Cancel any in-flight request if the widget goes away.
  useEffect(() => () => abort.current?.abort(), []);

  const ask = useCallback(
    async (question: string, prior: Message[]) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      failed.current = null;
      setLoadingLabel(THINKING_LABEL);
      setStatus("loading");
      // Id of the assistant bubble being filled while the reply streams in.
      let streamId: number | null = null;
      let streamed = "";
      try {
        const reply = await sendChatStream(
          config.apiBase,
          question,
          toHistory(prior),
          (text) => {
            streamed += text;
            if (streamId === null) {
              const id = nextId.current++;
              streamId = id;
              setMessages((current) => [
                ...current,
                { id, role: "assistant", content: streamed },
              ]);
              setStatus("streaming");
            } else {
              const id = streamId;
              const content = streamed;
              setMessages((current) =>
                current.map((m) => (m.id === id ? { ...m, content } : m)),
              );
            }
          },
          (stage) => setLoadingLabel(STAGE_LABELS[stage] ?? THINKING_LABEL),
          controller.signal,
        );
        // The final answer replaces the streamed text (it is the trimmed full reply).
        const id = streamId ?? nextId.current++;
        const finalMessage: Message = {
          id,
          role: "assistant",
          content: reply.answer,
          sources: reply.sources,
        };
        setMessages((current) =>
          current.some((m) => m.id === id)
            ? current.map((m) => (m.id === id ? finalMessage : m))
            : [...current, finalMessage],
        );
        setStatus("idle");
        if (voiceOnRef.current) void speak(toSpeakable(reply.answer));
      } catch (error) {
        if (controller.signal.aborted) return;
        if (streamId !== null) {
          // Drop the half-written reply; "Try again" asks the question afresh.
          const partial = streamId;
          setMessages((current) => current.filter((m) => m.id !== partial));
        }
        failed.current = { question, prior };
        setErrorText(
          error instanceof ChatApiError
            ? error.message
            : "Something went wrong. Please try again.",
        );
        setStatus("error");
      }
    },
    [config.apiBase, speak],
  );

  const submit = useCallback(
    (text: string) => {
      const question = text.trim();
      if (!question || status === "loading" || status === "streaming") return;
      const userMessage: Message = {
        id: nextId.current++,
        role: "user",
        content: question,
      };
      stopSpeech();
      setMessages((current) => [...current, userMessage]);
      setInput("");
      void ask(question, messages);
    },
    [ask, messages, status, stopSpeech],
  );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit(input);
  };

  const retry = () => {
    if (failed.current) void ask(failed.current.question, failed.current.prior);
  };

  return (
    <div className="nais-root">
      <button
        ref={bubbleRef}
        className={`bubble${open ? " is-open" : ""}`}
        type="button"
        aria-label={open ? "Close NAIS Assistant" : "Ask NAIS, open the assistant"}
        aria-expanded={open}
        aria-controls="nais-chat-window"
        onClick={() => (open ? close() : setOpen(true))}
      >
        {open ? <CloseIcon /> : <ChatIcon />}
        {!open && <span className="bubble-label">Start Chat</span>}
      </button>

      {open && (
        <section
          id="nais-chat-window"
          className="window"
          role="dialog"
          aria-label="NAIS Assistant chat"
        >
          <div className="rainbow" aria-hidden="true" />
          <header className="header">
            <h2>NAIS Assistant</h2>
            <button
              className="icon-button speaker"
              type="button"
              aria-label={voiceOn ? "Turn off voice" : "Turn on voice"}
              aria-pressed={voiceOn}
              onClick={toggleVoice}
            >
              <SpeakerIcon active={voiceOn} />
            </button>
            <button
              className="icon-button"
              type="button"
              aria-label="Close chat"
              onClick={close}
            >
              <CloseIcon />
            </button>
          </header>

          <div
            ref={listRef}
            className="messages"
            role="log"
            aria-live="polite"
            aria-label="Conversation"
          >
            <div className="msg assistant">
              <p>{WELCOME}</p>
              {hintShown && (
                // Stays in the layout (just hidden) so the content below never
                // jumps when the first click lifts the autoplay block.
                <p
                  className="voice-hint"
                  role="status"
                  style={{ visibility: needsTap ? "visible" : "hidden" }}
                >
                  Click anywhere to hear this read aloud.
                </p>
              )}
            </div>

            {messages.map((message) => (
              <div
                key={message.id}
                data-message-id={message.id}
                className={`msg ${message.role}`}
              >
                {message.role === "assistant" ? (
                  <FormattedText text={message.content} />
                ) : (
                  <p>{message.content}</p>
                )}
                {message.sources && message.sources.length > 0 && (
                  <SourceList sources={message.sources} />
                )}
              </div>
            ))}

            {status === "loading" && (
              <div className="msg assistant typing" role="status">
                <span className="typing-label">{loadingLabel}</span>
                <span className="dot" aria-hidden="true" />
                <span className="dot" aria-hidden="true" />
                <span className="dot" aria-hidden="true" />
              </div>
            )}

            {status === "error" && (
              <div className="error" role="alert">
                <p>{errorText}</p>
                <button type="button" className="retry" onClick={retry}>
                  Try again
                </button>
                <p className="fallback-title">In the meantime:</p>
                <ul>
                  {config.fallbackLinks.map((link) => (
                    <li key={link.url + link.label}>
                      <a href={link.url} target="_blank" rel="noopener noreferrer">
                        {link.label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {messages.length === 0 && (
            // Pinned just above the input so the topics are always in reach.
            <div className="starters" role="group" aria-label="Suggested topics">
              <p className="starters-title">Suggested topics</p>
              <div className="starter-grid">
                {STARTERS.map((starter) => (
                  <button
                    key={starter.question}
                    type="button"
                    className="starter"
                    onClick={() => submit(starter.question)}
                  >
                    {starter.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          <form className="composer" onSubmit={onSubmit}>
            <textarea
              ref={inputRef}
              rows={1}
              value={input}
              maxLength={MAX_MESSAGE_CHARS}
              placeholder={
                mic.listening ? "Listening…" : mic.transcribing ? "Transcribing…" : "Type your question…"
              }
              aria-label="Your question"
              autoComplete="off"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                // Enter sends; ignore Enter that only confirms an IME composition.
                // Shift+Enter inserts a newline.
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  mic.stop();
                  submit(input);
                }
              }}
            />
            {mic.supported && (
              <button
                type="button"
                className={`mic${mic.listening ? " listening" : ""}`}
                aria-label={mic.listening ? "Stop voice input" : "Speak your question"}
                aria-pressed={mic.listening}
                disabled={mic.transcribing}
                onClick={toggleMic}
              >
                <MicIcon />
              </button>
            )}
            <button
              type="submit"
              className="send"
              disabled={status === "loading" || status === "streaming" || !input.trim()}
            >
              Send
            </button>
          </form>
          {mic.error && (
            <p className="mic-error" role="alert">
              {mic.error}
            </p>
          )}
          <p className="privacy">{PRIVACY_NOTICE}</p>
        </section>
      )}
    </div>
  );
}
