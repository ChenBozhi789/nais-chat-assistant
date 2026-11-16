import { useCallback, useEffect, useRef, useState } from "react";

/** Minimal typings: the Web Speech recognition API is not in TypeScript's DOM lib. */
interface RecognitionResult {
  isFinal: boolean;
  0: { transcript: string };
}
interface RecognitionEvent {
  resultIndex: number;
  results: ArrayLike<RecognitionResult>;
}
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionCtor = new () => Recognition;

function getRecognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const ERROR_MESSAGES: Record<string, string> = {
  "not-allowed": "Microphone access is blocked. Allow it in your browser to use voice input.",
  "service-not-allowed": "Microphone access is blocked. Allow it in your browser to use voice input.",
  "no-speech": "I didn't hear anything. Tap the microphone and try again.",
  "audio-capture": "No microphone was found.",
  network: "Voice input needs an internet connection.",
};

/**
 * Speech-to-text using the browser's built-in recognition (no audio goes to
 * our backend). `supported` is false in browsers without it (e.g. Firefox),
 * so the UI can hide the button.
 *
 * `onText` receives the transcript so far (interim and final) for the current
 * listening session.
 */
export function useSpeechInput(lang: string, onText: (text: string) => void) {
  const supported = getRecognitionCtor() !== null;
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const recognition = useRef<Recognition | null>(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  const stop = useCallback(() => recognition.current?.stop(), []);

  const start = useCallback(() => {
    const Ctor = getRecognitionCtor();
    if (!Ctor || recognition.current) return;
    const rec = new Ctor();
    rec.lang = lang;
    rec.continuous = false;
    rec.interimResults = true;
    rec.onresult = (event) => {
      let transcript = "";
      for (let i = 0; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      onTextRef.current(transcript.trim());
    };
    rec.onerror = (event) => {
      if (event.error === "aborted") return;
      setError(ERROR_MESSAGES[event.error] ?? "Voice input failed. Please try again.");
    };
    rec.onend = () => {
      recognition.current = null;
      setListening(false);
    };
    recognition.current = rec;
    setError("");
    setListening(true);
    try {
      rec.start();
    } catch {
      recognition.current = null;
      setListening(false);
    }
  }, [lang]);

  // Release the microphone when the widget goes away.
  useEffect(() => () => recognition.current?.abort(), []);

  return { supported, listening, error, start, stop };
}
