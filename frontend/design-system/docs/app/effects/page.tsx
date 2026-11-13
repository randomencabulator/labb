import type { Metadata } from "next";
import { motion, shadow, z } from "@labb/tokens";
import { Button } from "@labb/ui";
import { CopyValue } from "@/lib/CodeBlock";
import { motionNote, page } from "@/lib/content";
import { PageHead } from "@/lib/PageHead";

const info = page("/effects");

export const metadata: Metadata = { title: info.title };

export default function Effects() {
  return (
    <>
      <PageHead path="/effects" title={info.title} lede={info.lede} />

      <section className="docs-section">
        <h2>Shadows</h2>
        <div className="shadow-grid">
          {Object.entries(shadow).map(([name, s]) => (
            <div key={name} className="shadow-item">
              <div className="shadow-box" style={{ boxShadow: `var(--lb-shadow-${name})` }} />
              <b>{name}</b>
              <CopyValue value={`var(--lb-shadow-${name})`} />
              <span className="docs-muted docs-small">{s.use}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="docs-section">
        <h2>Motion</h2>
        <p className="docs-muted">{motionNote}</p>
        <div className="docs-preview">
          <Button variant="primary">Hover and press me</Button>
        </div>
        <table className="docs-table">
          <thead>
            <tr>
              <th>Token</th>
              <th>Value</th>
              <th>Use</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(motion).map(([name, m]) => (
              <tr key={name}>
                <td>
                  <CopyValue value={`var(--lb-motion-${name})`} />
                </td>
                <td className="docs-mono">{m.value}</td>
                <td className="docs-muted">{m.use}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="docs-section">
        <h2>Layers</h2>
        <table className="docs-table">
          <thead>
            <tr>
              <th>Token</th>
              <th>z-index</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(z).map(([name, v]) => (
              <tr key={name}>
                <td>
                  <CopyValue value={`var(--lb-z-${name})`} />
                </td>
                <td className="docs-mono">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
