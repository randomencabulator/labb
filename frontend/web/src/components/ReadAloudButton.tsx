import { useState } from "react";
import { readAloud, stopPlaying, usePlayer } from "../speech";
import { SpeakerIcon, StopIcon } from "./Icons";

/** Reads a reply aloud with the server's speaking model; tapped again (even while the voice is
 * still being made), it stops */
export function ReadAloudButton({ getText, id }: { getText: () => string; id: string }) {
  const player = usePlayer();
  const mine = player?.key === id;
  const [problem, setProblem] = useState<string | null>(null);

  const toggle = async () => {
    if (mine) return stopPlaying();
    setProblem(null);
    try {
      await readAloud(getText(), id);
    } catch (e) {
      setProblem((e as Error).message);
      setTimeout(() => setProblem(null), 5000);
    }
  };

  const label = mine ? "Stop" : problem ? "Couldn't read it" : "Read aloud";
  return (
    <button type="button" className={mine ? "copy reading" : "copy"} onClick={toggle} title={problem ?? (mine ? "Stop reading (Esc)" : undefined)}>
      {mine ? <StopIcon /> : <SpeakerIcon />} {label}
    </button>
  );
}
