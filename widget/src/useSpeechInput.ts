import { useCallback, useEffect, useRef, useState } from "react";
import { transcribeAudio } from "./api";

const TARGET_SAMPLE_RATE = 16000; // what the Azure Speech short-audio API expects
const MAX_RECORDING_MS = 30_000; // stop automatically; keeps clips well under Azure's 60 s limit
// Voice activity detection: a clip ends once the speaker has been quiet for a while.
const VOICE_RMS_THRESHOLD = 0.02; // loudness (0..1) above which we count it as speech
const MIN_SPEECH_MS = 200; // ignore clicks and bumps shorter than this
const SILENCE_AFTER_SPEECH_MS = 1200; // quiet this long after speech = finished talking
const POLL_MS = 100;

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

interface SpeechInputOptions {
  /** Final transcript of one spoken turn. */
  onText: (text: string) => void;
  /** The visitor started talking (fires once per recording). */
  onSpeechStart?: () => void;
}

/**
 * Speech-to-text via our backend (Azure Speech). `start()` records one spoken
 * turn: it stops by itself after the speaker goes quiet, then sends the clip to
 * POST /stt and passes the transcript to `onText`. Clips with no speech are
 * dropped without calling the backend. `cancel()` throws everything away.
 * `supported` is false where recording isn't available.
 */
export function useSpeechInput(
  apiBase: string,
  { onText, onSpeechStart }: SpeechInputOptions,
) {
  const supported =
    typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const vadTimer = useRef<number | undefined>(undefined);
  const audioContext = useRef<AudioContext | null>(null);
  const heardSpeech = useRef(false);
  const starting = useRef(false);
  const abort = useRef<AbortController | null>(null);
  const cancelled = useRef(false);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;
  const onSpeechStartRef = useRef(onSpeechStart);
  onSpeechStartRef.current = onSpeechStart;

  const releaseMic = useCallback(() => {
    window.clearTimeout(timer.current);
    window.clearInterval(vadTimer.current);
    void audioContext.current?.close();
    audioContext.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    recorder.current = null;
  }, []);

  const stop = useCallback(() => {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }, []);

  /** Drop the recording and any transcription in flight; no transcript is delivered. */
  const cancel = useCallback(() => {
    cancelled.current = true;
    abort.current?.abort();
    abort.current = null;
    if (recorder.current?.state === "recording") recorder.current.stop();
    releaseMic();
    setListening(false);
    setTranscribing(false);
    setError("");
  }, [releaseMic]);

  /** Poll the microphone level; stop recording once the speaker has gone quiet. */
  const watchVoice = useCallback(
    (media: MediaStream) => {
      const context = new AudioContext();
      void context.resume();
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      context.createMediaStreamSource(media).connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      let voicedMs = 0;
      let lastVoiceAt = 0;
      audioContext.current = context;
      vadTimer.current = window.setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        let sum = 0;
        for (const sample of samples) sum += sample * sample;
        const loud = Math.sqrt(sum / samples.length) > VOICE_RMS_THRESHOLD;
        const now = performance.now();
        if (loud) {
          voicedMs += POLL_MS;
          lastVoiceAt = now;
          if (!heardSpeech.current && voicedMs >= MIN_SPEECH_MS) {
            heardSpeech.current = true;
            onSpeechStartRef.current?.();
          }
        } else if (heardSpeech.current && now - lastVoiceAt > SILENCE_AFTER_SPEECH_MS) {
          stop();
        }
      }, POLL_MS);
    },
    [stop],
  );

  const start = useCallback(async () => {
    if (!supported || recorder.current || starting.current || transcribing) return;
    starting.current = true;
    setError("");
    cancelled.current = false;
    let media: MediaStream;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      starting.current = false;
      const name = (e as DOMException).name;
      setError(
        name === "NotFoundError" ? "No microphone was found." : MIC_BLOCKED,
      );
      return;
    }
    starting.current = false;
    if (cancelled.current) {
      // cancel() ran while we were waiting for the microphone permission.
      media.getTracks().forEach((t) => t.stop());
      return;
    }
    const rec = new MediaRecorder(media);
    const chunks: Blob[] = [];
    stream.current = media;
    recorder.current = rec;
    heardSpeech.current = false;
    rec.ondataavailable = (event) => chunks.push(event.data);
    rec.onstop = async () => {
      const type = rec.mimeType;
      const hadSpeech = heardSpeech.current;
      releaseMic();
      setListening(false);
      // No speech in this clip: skip the backend call entirely.
      if (cancelled.current || chunks.length === 0 || !hadSpeech) return;
      setTranscribing(true);
      const controller = new AbortController();
      abort.current = controller;
      try {
        const wav = await toWav(new Blob(chunks, { type }));
        const text = await transcribeAudio(apiBase, wav, controller.signal);
        if (controller.signal.aborted) return;
        if (text) onTextRef.current(text);
      } catch {
        if (!controller.signal.aborted) {
          setError("Voice input failed. Please try again.");
        }
      } finally {
        if (abort.current === controller) abort.current = null;
        if (!controller.signal.aborted) setTranscribing(false);
      }
    };
    rec.start();
    watchVoice(media);
    setListening(true);
    timer.current = window.setTimeout(stop, MAX_RECORDING_MS);
  }, [apiBase, releaseMic, stop, supported, transcribing, watchVoice]);

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

  return { supported, listening, transcribing, error, start, stop, cancel };
}
