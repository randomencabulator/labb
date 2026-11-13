import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Code } from "@labb/ui";
import { CodeBlock } from "./CodeBlock";
import { importsOf } from "./markdown";
import { Demo } from "./demos";
import { PageHead } from "./PageHead";
import { components } from "./registry";

// A component page's <title>
export function componentMetadata(slug: string): Metadata {
  return { title: components.find((c) => c.slug === slug)?.name ?? "Components" };
}

// One component's page: its intro, then each example live over its code. Each component has a
// folder of its own in app/components/ (not [slug]), so /components/<slug>.md reaches app/[...path]
export function ComponentPage({ slug }: { slug: string }) {
  const at = components.findIndex((c) => c.slug === slug);
  if (at < 0) notFound();
  const doc = components[at];
  const prev = components[at - 1];
  const next = components[at + 1];

  return (
    <>
      <PageHead path={`/components/${doc.slug}`} title={doc.name} lede={doc.about} back={{ href: "/components", label: "React components" }}>
        <p className="docs-web">
          React, from <Code>@labb/ui</Code>: <Code>{`import { ${importsOf(doc).join(", ")} } from "@labb/ui";`}</Code>
        </p>
        <p className="docs-web">
          In the web app: <Code>{doc.web}</Code>
        </p>
        {doc.base && (
          <p className="docs-web">
            Built on Base UI&apos;s{" "}
            <a className="lb-link" href={doc.base.url} target="_blank" rel="noreferrer">
              {doc.base.name}
            </a>
          </p>
        )}
      </PageHead>
      {doc.examples.map((ex, i) => (
        <section key={ex.title} className="docs-section">
          <h2>{ex.title}</h2>
          {ex.about && <p className="docs-muted">{ex.about}</p>}
          <div className="docs-example">
            <div className="docs-preview">
              <Demo slug={doc.slug} index={i} />
            </div>
            <CodeBlock code={ex.code} />
          </div>
        </section>
      ))}
      <nav className="docs-pager">
        {prev ? <Link href={`/components/${prev.slug}`}>← {prev.name}</Link> : <span />}
        {next && <Link href={`/components/${next.slug}`}>{next.name} →</Link>}
      </nav>
    </>
  );
}
