import { useEffect, useState } from "react";
import type { Stats } from "../api";
import { formatStats, seconds } from "../format";

/** Tokens and timing of a reply, or a running timer while it's being written */
export function StatsLine({ stats, startedAt, streaming }: { stats?: Stats; startedAt?: number; streaming: boolean }) {
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    if (!streaming) return;
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [streaming]);

  if (streaming && startedAt) return <span className="stats">{seconds(now - startedAt)}</span>;
  if (!stats) return null;
  return (
    <span className="stats" title={`${stats.llm_calls} model call${stats.llm_calls === 1 ? "" : "s"}`}>
      {formatStats(stats)}
    </span>
  );
}
