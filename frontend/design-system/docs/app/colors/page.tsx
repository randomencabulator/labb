import type { Metadata } from "next";
import { colors, contrast, type ColorToken } from "@labb/tokens";
import { Badge, Code } from "@labb/ui";
import { CopyValue } from "@/lib/CodeBlock";
import { colorToken, contrastBase, hazard, page, refVar } from "@/lib/content";
import { PageHead } from "@/lib/PageHead";

const info = page("/colors");

export const metadata: Metadata = { title: info.title };

function Ratio({ token, theme }: { token: ColorToken; theme: "light" | "dark" }) {
  const base = contrastBase(token.name);
  if (!base) return null;
  const ratio = contrast(token[theme], colorToken(base)[theme]);
  if (ratio == null) return null;
  // Text needs 4.5; lines, marks and large text 3
  const tone = ratio >= 4.5 ? "default" : ratio >= 3 ? "outline" : "error";
  return (
    <Badge tone={tone} mono title={`Against ${base}`}>
      {ratio.toFixed(1)}:1 on {base}
    </Badge>
  );
}

function Swatch({ token, theme }: { token: ColorToken; theme: "light" | "dark" }) {
  return (
    <div className="swatch" data-lb-theme={theme}>
      <div className="swatch-chip" style={{ background: `var(--lb-sys-color-${token.name})` }} />
      <div className="swatch-meta">
        <span className="lb-label">{theme}</span>
        <code>{token[theme]}</code>
        {(theme === "light" ? token.lightRef : token.darkRef) && (
          <span className="swatch-ref">{refVar((theme === "light" ? token.lightRef : token.darkRef)!)}</span>
        )}
        <Ratio token={token} theme={theme} />
      </div>
    </div>
  );
}

export default function Colors() {
  return (
    <>
      <PageHead path="/colors" title={info.title} lede={info.lede} />
      {colors.map((group) => (
        <section key={group.name} className="docs-section">
          <h2>{group.name}</h2>
          <p className="docs-muted">{group.about}</p>
          <div className="color-list">
            {group.tokens.map((t) => (
              <article key={t.name} className="color-row">
                <div className="color-name">
                  <b>{t.name}</b>
                  <CopyValue value={`var(--lb-sys-color-${t.name})`} />
                  <span className="docs-muted">{t.use}</span>
                  {t.web && <span className="docs-web">web: {t.web}</span>}
                </div>
                <Swatch token={t} theme="light" />
                <Swatch token={t} theme="dark" />
              </article>
            ))}
          </div>
        </section>
      ))}
      <section className="docs-section">
        <h2>Hazard stripes</h2>
        <p className="docs-muted">{hazard}</p>
        <div className="lb-hazard hazard-demo" />
      </section>
    </>
  );
}
