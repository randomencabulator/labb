import type { Metadata } from "next";
import { border, radius, space } from "@labb/tokens";
import { CopyValue } from "@/lib/CodeBlock";
import { bordersNote, page, spaceUse } from "@/lib/content";
import { PageHead } from "@/lib/PageHead";

const info = page("/spacing");

export const metadata: Metadata = { title: info.title };

export default function Spacing() {
  return (
    <>
      <PageHead path="/spacing" title={info.title} lede={info.lede} />

      <section className="docs-section">
        <h2>Spacing</h2>
        <table className="docs-table">
          <thead>
            <tr>
              <th>Token</th>
              <th>Value</th>
              <th />
              <th>Use</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(space)
              .filter(([k]) => k !== "0")
              .map(([name, px]) => (
                <tr key={name}>
                  <td>
                    <CopyValue value={`var(--lb-space-${name})`} />
                  </td>
                  <td className="docs-mono">{px}px</td>
                  <td>
                    <span className="space-bar" style={{ width: px * 4 }} />
                  </td>
                  <td className="docs-muted">{spaceUse[name]}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </section>

      <section className="docs-section">
        <h2>Radii</h2>
        <div className="radius-grid">
          {Object.entries(radius).map(([name, r]) => (
            <div key={name} className="radius-item">
              <div className="radius-box" style={{ borderRadius: `var(--lb-radius-${name})` }} />
              <b>{name}</b>
              <CopyValue value={`var(--lb-radius-${name})`} />
              <span className="docs-mono docs-small">{r.value}</span>
              <span className="docs-muted docs-small">{r.use}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="docs-section">
        <h2>Borders</h2>
        <p className="docs-muted">{bordersNote}</p>
        <div className="radius-grid">
          {Object.entries(border).map(([name, w]) => (
            <div key={name} className="radius-item">
              <div
                className="radius-box"
                style={{
                  border: `${w} solid ${name === "thin" ? "var(--lb-sys-color-border)" : name === "focus" ? "var(--lb-sys-color-accent)" : "var(--lb-sys-color-text)"}`,
                }}
              />
              <b>{name}</b>
              <CopyValue value={`var(--lb-border-${name})`} />
              <span className="docs-mono docs-small">{w}</span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
