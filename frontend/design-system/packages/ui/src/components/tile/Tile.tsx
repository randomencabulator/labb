import type { ReactNode } from "react";

// One number with a mono label, for stats
export function Tile({ label, value, note }: { label: ReactNode; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="lb-tile">
      <div className="lb-tile-label">{label}</div>
      <div className="lb-tile-value">{value}</div>
      {note != null && <div className="lb-tile-note">{note}</div>}
    </div>
  );
}
