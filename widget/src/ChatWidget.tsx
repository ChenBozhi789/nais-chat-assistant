import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ChatApiError, fetchSpeech, sendChat, type HistoryTurn, type Source } from "./api";
import type { WidgetConfig } from "./config";
import { FormattedText } from "./FormattedText";
import { useSpeechInput } from "./useSpeechInput";

/** Language the browser should listen for (New Zealand English). */
const VOICE_INPUT_LANG = "en-NZ";

const MAX_MESSAGE_CHARS = 1000;
const MAX_HISTORY_TURNS = 6;
const MAX_HISTORY_CHARS = 2000;

const WELCOME =
  "Kia ora! My name is Jack, I am here to help you with your questions about the Nelson AI Sandbox.";

const STARTERS = [
  "Where is the Sandbox and when is it open?",
  "What workshops are coming up?",
  "What free AI tools can I use?",
];

const AUTO_OPEN_DELAY_MS = 300;
const AUTO_OPEN_KEY = "nais-assistant-greeted";

const PRIVACY_NOTICE =
  "AI answers can contain mistakes, so check key details with NAIS. " +
  "Your messages (and voice input, if used, which your browser may process " +
  "through its speech service) are sent to an AI service to write replies, so please don't " +
  "share personal information. This chat isn't saved after you leave the page.";

interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
}

type Status = "idle" | "loading" | "error";

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

  const nextId = useRef(1);
  const failed = useRef<{ question: string; prior: Message[] } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bubbleRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
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

  const mic = useSpeechInput(VOICE_INPUT_LANG, (text) => setInput(text.slice(0, MAX_MESSAGE_CHARS)));

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
    if (!list) return;
    const last = messages[messages.length - 1];
    const answer =
      status === "idle" && last?.role === "assistant"
        ? list.querySelector<HTMLElement>(`[data-message-id="${last.id}"]`)
        : null;
    if (answer) {
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
      setStatus("loading");
      try {
        const reply = await sendChat(
          config.apiBase,
          question,
          toHistory(prior),
          controller.signal,
        );
        setMessages((current) => [
          ...current,
          {
            id: nextId.current++,
            role: "assistant",
            content: reply.answer,
            sources: reply.sources,
          },
        ]);
        setStatus("idle");
        if (voiceOnRef.current) void speak(toSpeakable(reply.answer));
      } catch (error) {
        if (controller.signal.aborted) return;
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
      if (!question || status === "loading") return;
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
        className="bubble"
        type="button"
        aria-label={open ? "Close NAIS Assistant" : "Open NAIS Assistant"}
        aria-expanded={open}
        aria-controls="nais-chat-window"
        onClick={() => (open ? close() : setOpen(true))}
      >
        {open ? <CloseIcon /> : <ChatIcon />}
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

            {messages.length === 0 && (
              <div className="starters">
                {STARTERS.map((starter) => (
                  <button
                    key={starter}
                    type="button"
                    className="starter"
                    onClick={() => submit(starter)}
                  >
                    {starter}
                  </button>
                ))}
              </div>
            )}

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
                <span className="sr-only">NAIS Assistant is typing</span>
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

          <form className="composer" onSubmit={onSubmit}>
            <input
              ref={inputRef}
              type="text"
              value={input}
              maxLength={MAX_MESSAGE_CHARS}
              placeholder={mic.listening ? "Listening…" : "Type your question…"}
              aria-label="Your question"
              autoComplete="off"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                // Enter sends; ignore Enter that only confirms an IME composition.
                if (event.key === "Enter" && !event.nativeEvent.isComposing) {
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
                onClick={toggleMic}
              >
                <MicIcon />
              </button>
            )}
            <button
              type="submit"
              className="send"
              disabled={status === "loading" || !input.trim()}
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
