// Speech in the web chat: what the server can do, recording from the mic (stopping by itself
// after a pause, for voice mode), and playing what it reads aloud, one thing at a time.

import { useEffect, useState } from "react";
import { MODELS_CHANGED, type SpeechInfo, getSpeech, speakText, speechParts, transcribe } from "./api";

const NONE: SpeechInfo = { listen: false, speak: false, voice: "" };

/** What the server can do with speech; asked again when an admin changes the models */
export function useSpeech(): SpeechInfo {
  const [info, setInfo] = useState<SpeechInfo>(NONE);
  useEffect(() => {
    const load = () => getSpeech().then(setInfo, () => setInfo(NONE));
    load();
    window.addEventListener(MODELS_CHANGED, load);
    return () => window.removeEventListener(MODELS_CHANGED, load);
  }, []);
  return info;
}

// --- Recording -----------------------------------------------------------------------------

// Loudness (RMS, 0 to 1) that counts as speaking, how long a pause ends what's said, and limits
const SPEAKING_LEVEL = 0.02;
const PAUSE_MS = 1400;
const MAX_MS = 120_000;
const WAIT_FOR_SPEECH_MS = 30_000;

export type Recording = {
  /** Stop and get what was recorded (null if nothing was said, with `untilPause`) */
  done: Promise<{ blob: Blob; name: string } | null>;
  stop: () => void;
  cancel: () => void;
};

function recorderType(): { mimeType: string; name: string } {
  for (const [mimeType, name] of [
    ["audio/webm;codecs=opus", "recording.webm"],
    ["audio/webm", "recording.webm"],
    ["audio/mp4", "recording.m4a"],
    ["audio/ogg;codecs=opus", "recording.ogg"],
  ]) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(mimeType)) return { mimeType, name };
  }
  return { mimeType: "", name: "recording.webm" };
}

/**
 * Record from the mic. With `untilPause` it stops by itself once something was said and a pause
 * followed (or nothing was said for a while). `onLevel` gets the loudness as it records.
 */
export async function record({ untilPause = false, onLevel }: { untilPause?: boolean; onLevel?: (level: number) => void } = {}): Promise<Recording> {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser can't record here: the mic needs https or localhost (see HTTPS in the guide)");
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const { mimeType, name } = recorderType();
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks: Blob[] = [];
  recorder.ondataavailable = event => event.data.size && chunks.push(event.data);

  const context = new AudioContext();
  const analyser = context.createAnalyser();
  analyser.fftSize = 2048;
  context.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  const started = performance.now();
  let spoke = false;
  let quietSince = performance.now();
  let cancelled = false;
  let frame = 0;

  const finish = () => {
    cancelAnimationFrame(frame);
    if (recorder.state !== "inactive") recorder.stop();
  };
  const watch = () => {
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const s of samples) sum += s * s;
    const level = Math.sqrt(sum / samples.length);
    onLevel?.(level);
    const now = performance.now();
    if (level > SPEAKING_LEVEL) {
      spoke = true;
      quietSince = now;
    }
    if (now - started > MAX_MS) return finish();
    if (untilPause && spoke && now - quietSince > PAUSE_MS) return finish();
    if (untilPause && !spoke && now - started > WAIT_FOR_SPEECH_MS) return finish();
    frame = requestAnimationFrame(watch);
  };

  const done = new Promise<{ blob: Blob; name: string } | null>(resolve => {
    recorder.onstop = () => {
      stream.getTracks().forEach(track => track.stop());
      context.close();
      onLevel?.(0);
      if (cancelled || (untilPause && !spoke) || !chunks.length) return resolve(null);
      resolve({ blob: new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" }), name });
    };
  });
  recorder.start(250);
  frame = requestAnimationFrame(watch);
  return {
    done,
    stop: finish,
    cancel: () => {
      cancelled = true;
      finish();
    },
  };
}

function base64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = reader.result as string;
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/** What's said in a recording, by the server's listening model */
export async function toText(recording: { blob: Blob; name: string }): Promise<string> {
  return (await transcribe(await base64(recording.blob), recording.name)).trim();
}

// --- Playing ---------------------------------------------------------------------------------
//
// One thing is read aloud at a time. A reply is read in parts (speechParts): the first is short,
// so it starts soon, and each next one is made while the one before plays. It can be paused,
// sped up and stopped from anywhere (the now-playing bar), and stopping also cancels what's still
// being made.

export type Player = {
  key: string;
  status: "loading" | "playing" | "paused";
  // The part playing, of how many (0 of 0 until they're known), and how far into it (0 to 1)
  part: number;
  parts: number;
  within: number;
  rate: number;
  // The start of what's read, to show
  preview: string;
};

export const RATES = [1, 1.25, 1.5, 2];
const RATE_KEY = "labb.speechRate";
let rate = Number(localStorage.getItem(RATE_KEY)) || 1;

let player: Player | null = null;
let audio: HTMLAudioElement | null = null;
let abort: AbortController | null = null;
let finish: (() => void) | null = null;
let urls: string[] = [];
// Each readAloud gets a number; one that was stopped sees a newer one and quits
let current = 0;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach(listener => listener());

function set(next: Player | null) {
  player = next;
  changed();
}

function preview(markdown: string) {
  const text = markdown.replace(/```[\s\S]*?(```|$)/g, " ").replace(/[#*_>`|[\]()-]+/g, " ").replace(/\s+/g, " ").trim();
  return text.length > 90 ? `${text.slice(0, 90).trimEnd()}…` : text;
}

/** Stop what's being read aloud, and what's still being made for it */
export function stopPlaying() {
  current++;
  abort?.abort();
  abort = null;
  if (audio) {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }
  urls.forEach(url => URL.revokeObjectURL(url));
  urls = [];
  const done = finish;
  finish = null;
  set(null);
  done?.();
}

export function pausePlaying() {
  if (player?.status !== "playing" || !audio) return;
  audio.pause();
  set({ ...player, status: "paused" });
}

export function resumePlaying() {
  if (player?.status !== "paused" || !audio) return;
  audio.play().catch(() => stopPlaying());
  set({ ...player, status: "playing" });
}

/** Faster or back to normal; kept for next time */
export function setRate(next: number) {
  rate = next;
  localStorage.setItem(RATE_KEY, String(next));
  if (audio) audio.playbackRate = next;
  if (player) set({ ...player, rate: next });
}

/** Play one part to its end; false if it was stopped */
function playPart(url: string, mine: number): Promise<boolean> {
  return new Promise((resolve, reject) => {
    if (mine !== current) return resolve(false);
    audio ??= new Audio();
    const el = audio;
    el.src = url;
    el.playbackRate = rate;
    el.preservesPitch = true;
    el.ontimeupdate = () => {
      if (player && mine === current && el.duration) set({ ...player, within: el.currentTime / el.duration });
    };
    el.onended = () => resolve(mine === current);
    el.onerror = () => (mine === current ? reject(new Error("The audio couldn't be played")) : resolve(false));
    el.play().catch(error => (mine === current ? reject(error) : resolve(false)));
  });
}

/**
 * Read Markdown aloud; resolves when it ends or is stopped, rejects if it can't be read.
 * `key` names what's playing (a reply's id, or "voice" for voice mode).
 */
export async function readAloud(text: string, key: string): Promise<void> {
  stopPlaying();
  const mine = current;
  const controller = new AbortController();
  abort = controller;
  const stopped = new Promise<void>(resolve => (finish = resolve));
  set({ key, status: "loading", part: 0, parts: 0, within: 0, rate, preview: preview(text) });
  const run = async () => {
    const parts = await speechParts(text, controller.signal);
    if (mine !== current) return;
    if (!parts.length) throw new Error("There's nothing to say");
    const make = (i: number) => speakText(parts[i], { markdown: false, signal: controller.signal }).then(blob => {
      const url = URL.createObjectURL(blob);
      urls.push(url);
      return url;
    });
    let next = make(0);
    for (let i = 0; i < parts.length; i++) {
      if (mine !== current || !player) return;
      if (player?.status !== "paused") set({ ...player!, status: player?.status === "playing" ? "playing" : "loading", part: i, parts: parts.length, within: 0 });
      const url = await next;
      if (mine !== current) return;
      // The next part is made while this one plays
      if (i + 1 < parts.length) next = make(i + 1);
      set({ ...player!, status: player?.status === "paused" ? "paused" : "playing", part: i, parts: parts.length, within: 0 });
      if (player?.status === "paused") await new Promise<void>(resolve => {
        const wait = () => (player?.status !== "paused" || mine !== current) && (listeners.delete(wait), resolve());
        listeners.add(wait);
      });
      if (!(await playPart(url, mine))) return;
    }
  };
  try {
    await Promise.race([run(), stopped]);
  } catch (error) {
    if (mine !== current || (error as Error).name === "AbortError") return;
    stopPlaying();
    throw error;
  }
  if (mine === current) stopPlaying();
}

/** What's being read aloud, kept up to date (null when nothing is) */
export function usePlayer(): Player | null {
  const [state, setState] = useState<Player | null>(player);
  useEffect(() => {
    const update = () => setState(player);
    listeners.add(update);
    update();
    return () => void listeners.delete(update);
  }, []);
  return state;
}

/** The key of what's being read aloud */
export function usePlaying(): string | null {
  return usePlayer()?.key ?? null;
}
