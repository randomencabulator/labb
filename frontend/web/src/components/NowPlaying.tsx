// What's being read aloud, above the message box wherever the chat is scrolled: how far it is,
// pause, speed, and stop (Esc stops it too). Voice mode shows its own.
import { useEffect } from "react";
import { RATES, pausePlaying, resumePlaying, setRate, stopPlaying, usePlayer } from "../speech";
import { CloseIcon, PauseIcon, PlayIcon } from "./Icons";

export function NowPlaying() {
  const player = usePlayer();
  const shown = player && player.key !== "voice" ? player : null;

  useEffect(() => {
    if (!shown) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      // Esc stops the reading before it stops a reply
      event.preventDefault();
      event.stopImmediatePropagation();
      stopPlaying();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [shown !== null]);

  if (!shown) return null;
  const loading = shown.status === "loading";
  const progress = shown.parts ? (shown.part + shown.within) / shown.parts : 0;
  const nextRate = RATES[(RATES.indexOf(shown.rate) + 1) % RATES.length] ?? 1;
  return (
    <div className={`now-playing ${shown.status}`} role="region" aria-label="Reading aloud">
      <span className="np-bars" aria-hidden><i /><i /><i /><i /></span>
      <span className="np-text">
        <b>{loading && shown.part === 0 ? "Getting the voice ready…" : shown.status === "paused" ? "Paused" : "Reading aloud"}</b>
        <span>{shown.preview}</span>
      </span>
      {!(loading && shown.part === 0) && (
        <button type="button" className="np-button" aria-label={shown.status === "paused" ? "Go on" : "Pause"}
          title={shown.status === "paused" ? "Go on" : "Pause"} onClick={shown.status === "paused" ? resumePlaying : pausePlaying}>
          {shown.status === "paused" ? <PlayIcon /> : <PauseIcon />}
        </button>
      )}
      <button type="button" className="np-rate" title="Speed" aria-label={`Speed ${shown.rate}×, change to ${nextRate}×`} onClick={() => setRate(nextRate)}>
        {shown.rate}×
      </button>
      <button type="button" className="np-button" aria-label="Stop reading" title="Stop (Esc)" onClick={stopPlaying}>
        <CloseIcon />
      </button>
      <span className="np-progress" aria-hidden style={{ transform: `scaleX(${progress})` }} />
    </div>
  );
}
