import type { Metadata } from "next";
import { font } from "@labb/tokens";
import { Code, Label } from "@labb/ui";
import { CopyValue } from "@/lib/CodeBlock";
import { page, sizesNote } from "@/lib/content";
import { PageHead } from "@/lib/PageHead";

const info = page("/typography");

export const metadata: Metadata = { title: info.title };

export default function Typography() {
  return (
    <>
      <PageHead path="/typography" title={info.title} lede={info.lede} />

      <section className="docs-section">
        <h2>Families</h2>
        <div className="type-families">
          {Object.entries(font.family).map(([name, stack]) => (
            <div key={name} className="type-family" style={{ fontFamily: `var(--lb-font-${name})` }}>
              <span className="type-family-sample">Aa Bb 0123</span>
              <b>{name}</b>
              <CopyValue value={`var(--lb-font-${name})`} />
              <span className="docs-muted docs-small">{stack}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="docs-section">
        <h2>Sizes</h2>
        <p className="docs-muted">{sizesNote}</p>
        <table className="docs-table">
          <thead>
            <tr>
              <th>Token</th>
              <th>Size / line</th>
              <th>Sample</th>
              <th>Use</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(font.size).map(([name, s]) => (
              <tr key={name}>
                <td>
                  <CopyValue value={`var(--lb-text-${name})`} />
                </td>
                <td className="docs-mono">
                  {s.size}px / {s.line}
                </td>
                <td style={{ fontSize: s.size, lineHeight: s.line }}>The lab is open</td>
                <td className="docs-muted">{s.use}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="docs-section">
        <h2>Weights</h2>
        <div className="type-weights">
          {Object.entries(font.weight).map(([name, w]) => (
            <div key={name}>
              <span style={{ fontWeight: w }}>labb</span>
              <span className="docs-mono docs-small">
                {name} · {w}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="docs-section">
        <h2>Styles</h2>
        <div className="type-styles">
          <div>
            <span className="type-heading">Heavy and tight headings</span>
            <span className="docs-mono docs-small">
              weight black · tracking {font.tracking.heading}
            </span>
          </div>
          <div>
            <Label>Mono labels over sections</Label>
            <span className="docs-mono docs-small">
              2xs mono · bold · uppercase · tracking {font.tracking.label}
            </span>
          </div>
          <div>
            <span>
              Inline code like <Code>labb --update</Code> is mono at 0.9em on code-bg.
            </span>
          </div>
          <div>
            <span>
              Links are in the accent{" "}
              <a className="lb-link" href="#styles">
                with a highlighter under them
              </a>{" "}
              on light.
            </span>
          </div>
        </div>
      </section>
    </>
  );
}
