import Link from "next/link";
import { brand } from "@labb/tokens";
import { Approval, Button, Card, Code, Label, Logo, Wordmark } from "@labb/ui";
import { CodeBlock } from "@/lib/CodeBlock";
import { markdownPath, overview, principles, usage } from "@/lib/content";
import { Prose } from "@/lib/PageHead";

const pages = [
  { href: "/colors", eyebrow: "01", title: "Colours", body: "Every colour token in light and dark, with contrast" },
  { href: "/typography", eyebrow: "02", title: "Typography", body: "Families, the size scale, weights and tracking" },
  { href: "/spacing", eyebrow: "03", title: "Spacing and radii", body: "The 2px grid, corners and borders" },
  { href: "/effects", eyebrow: "04", title: "Shadows and motion", body: "Hard shadows, timings and layers" },
  { href: "/components", eyebrow: "05", title: "React components", body: "Buttons, fields, menus, dialogs and more, on Base UI" },
];

export default function Overview() {
  return (
    <>
      <header className="docs-hero">
        <link rel="alternate" type="text/markdown" href={`${process.env.PAGES_BASE_PATH ?? ""}${markdownPath(overview.path)}`} />
        <div className="docs-page-top">
          <span />
          <a className="docs-md-link" href={`${process.env.PAGES_BASE_PATH ?? ""}${markdownPath(overview.path)}`}>
            View as Markdown
          </a>
        </div>
        <div className="docs-hero-mark">
          <Logo size={56} />
          <h1>
            <Wordmark /> design
          </h1>
        </div>
        <p className="docs-lede">
          <Prose text={overview.lede} />
        </p>
        <div className="docs-hero-stripe lb-hazard" aria-hidden="true" />
      </header>

      <section className="docs-section">
        <Label as="h3">Principles</Label>
        <div className="docs-grid">
          {principles.map((p) => (
            <Card key={p.title} title={p.title}>
              {p.body}
            </Card>
          ))}
        </div>
      </section>

      <section className="docs-section">
        <Label as="h3">Explore</Label>
        <div className="docs-grid">
          {pages.map((p) => (
            <Link key={p.href} href={p.href} className="lb-card interactive docs-card-link">
              <span className="lb-card-eyebrow">{p.eyebrow}</span>
              <b className="lb-card-title">{p.title}</b>
              <span className="lb-card-body">{p.body}</span>
            </Link>
          ))}
        </div>
      </section>

      <section className="docs-section">
        <Label as="h3">At a glance</Label>
        <div className="docs-glance">
          <div className="docs-glance-swatches">
            {Object.entries(brand).map(([name, hex]) => (
              <div key={name} className="docs-glance-swatch" style={{ background: hex }}>
                <span style={{ color: name === "black" ? brand.paper : brand.black }}>
                  {name} <b>{hex}</b>
                </span>
              </div>
            ))}
          </div>
          <Approval
            title="Run this command?"
            actions={
              <>
                <Button variant="primary">Run</Button>
                <Button>Not now</Button>
              </>
            }
          >
            <Code>git push origin main</Code>
          </Approval>
        </div>
      </section>

      <section className="docs-section">
        <Label as="h3">Using it</Label>
        <p>
          <Prose text={usage.intro} />
        </p>
        <CodeBlock code={usage.tsx} />
        <CodeBlock lang="css" code={usage.css} />
      </section>
    </>
  );
}
