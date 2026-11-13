import type { Metadata } from "next";
import { contrast, palette, refColors } from "@labb/tokens";
import { page, paletteNotes } from "@/lib/content";
import { CopyValue } from "@/lib/CodeBlock";
import { PageHead, Prose } from "@/lib/PageHead";

const info = page("/palette");

export const metadata: Metadata = { title: info.title };

// Dark or light writing, whichever reads better on the step
const ink = (bg: string) => ((contrast(bg, "#ffffff") ?? 0) > (contrast(bg, "#0b0b0a") ?? 0) ? "#ffffff" : "#0b0b0a");

function Step({ ramp, step, value, pinned }: { ramp: string; step: string; value: string; pinned: boolean }) {
  return (
    <div className="ramp-step" style={{ background: value, color: ink(value) }} title={`var(--lb-ref-${ramp}-${step})`}>
      <b>
        {step}
        {pinned && <span aria-label="pinned"> ●</span>}
      </b>
      <code>{value}</code>
    </div>
  );
}

export default function Palette() {
  return (
    <>
      <PageHead path="/palette" title={info.title} lede={info.lede} />
      <section className="docs-section">
        <p>
          <Prose text={paletteNotes.layers} />
        </p>
        <p className="docs-muted">
          <Prose text={paletteNotes.pinned} />
        </p>
      </section>
      {palette.map((r) => (
        <section key={r.name} className="docs-section ramp">
          <div className="ramp-head">
            <h2>{r.title}</h2>
            <CopyValue value={`var(--lb-ref-${r.name}-500)`} />
          </div>
          <p className="docs-muted">{r.about}</p>
          <div className="ramp-steps">
            {r.steps.map((s) => (
              <Step key={s.step} ramp={r.name} {...s} />
            ))}
          </div>
        </section>
      ))}
      <section className="docs-section">
        <h2>White and black</h2>
        <div className="ramp-steps ramp-singles">
          {refColors.map((c) => (
            <div key={c.name} className="ramp-step" style={{ background: c.value, color: ink(c.value) }} title={`var(--lb-ref-${c.name})`}>
              <b>{c.name}</b>
              <code>{c.value}</code>
            </div>
          ))}
        </div>
      </section>
      <section className="docs-section">
        <h2>Where they come from</h2>
        <p className="docs-muted">
          <Prose text={paletteNotes.source} />
        </p>
      </section>
    </>
  );
}
