import { useCallback, useEffect, useRef, useState } from "react";
import { transcribeAudio } from "./api";

const TARGET_SAMPLE_RATE = 16000; // what the Azure Speech short-audio API expects
const MAX_RECORDING_MS = 30_000; // stop automatically; keeps clips well under Azure's 60 s limit

/** Write mono Float32 samples as a 16-bit PCM WAV file. */
function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeText = (offset: number, text: string) =>
    [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  writeText(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeText(8, "WAVEfmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeText(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => {
    const clamped = Math.max(-1, Math.min(1, s));
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  });
  return new Blob([buffer], { type: "audio/wav" });
}

/** Decode whatever the browser recorded (webm/opus, mp4, ...) to 16 kHz mono WAV. */
async function toWav(recording: Blob): Promise<Blob> {
  const context = new OfflineAudioContext(1, 1, TARGET_SAMPLE_RATE);
  const decoded = await context.decodeAudioData(await recording.arrayBuffer());
  // Resample + downmix by rendering through an offline context at the target rate.
  const offline = new OfflineAudioContext(
    1,
    Math.ceil(decoded.duration * TARGET_SAMPLE_RATE),
    TARGET_SAMPLE_RATE,
  );
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const rendered = await offline.startRendering();
  return encodeWav(rendered.getChannelData(0), TARGET_SAMPLE_RATE);
}

const MIC_BLOCKED = "Microphone access is blocked. Allow it in your browser to use voice input.";

/**
 * Speech-to-text via our backend (Azure Speech). Records from the microphone,
 * and on stop sends the clip to POST /stt. `supported` is false where
 * recording isn't available, so the UI can hide the button.
 *
 * `onText` receives the final transcript once transcription finishes.
 */
export function useSpeechInput(apiBase: string, onText: (text: string) => void) {
  const supported =
    typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const abort = useRef<AbortController | null>(null);
  const cancelled = useRef(false);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  const releaseMic = useCallback(() => {
    window.clearTimeout(timer.current);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    recorder.current = null;
  }, []);

  const stop = useCallback(() => {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }, []);

  const start = useCallback(async () => {
    if (!supported || recorder.current || transcribing) return;
    setError("");
    cancelled.current = false;
    let media: MediaStream;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      const name = (e as DOMException).name;
      setError(
        name === "NotFoundError" ? "No microphone was found." : MIC_BLOCKED,
      );
      return;
    }
    const rec = new MediaRecorder(media);
    const chunks: Blob[] = [];
    stream.current = media;
    recorder.current = rec;
    rec.ondataavailable = (event) => chunks.push(event.data);
    rec.onstop = async () => {
      const type = rec.mimeType;
      releaseMic();
      setListening(false);
      if (cancelled.current || chunks.length === 0) return;
      setTranscribing(true);
      const controller = new AbortController();
      abort.current = controller;
      try {
        const wav = await toWav(new Blob(chunks, { type }));
        const text = await transcribeAudio(apiBase, wav, controller.signal);
        if (controller.signal.aborted) return;
        if (text) onTextRef.current(text);
        else setError("I didn't hear anything. Tap the microphone and try again.");
      } catch {
        if (!controller.signal.aborted) {
          setError("Voice input failed. Please try again.");
        }
      } finally {
        abort.current = null;
        setTranscribing(false);
      }
    };
    rec.start();
    setListening(true);
    timer.current = window.setTimeout(stop, MAX_RECORDING_MS);
  }, [apiBase, releaseMic, stop, supported, transcribing]);

  // Release the microphone and drop any in-flight request when the widget goes away.
  useEffect(
    () => () => {
      cancelled.current = true;
      abort.current?.abort();
      if (recorder.current?.state === "recording") recorder.current.stop();
      releaseMic();
    },
    [releaseMic],
  );

  return { supported, listening, transcribing, error, start, stop };
}
