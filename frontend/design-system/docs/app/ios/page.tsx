import type { Metadata } from "next";
import { CodeBlock } from "@/lib/CodeBlock";
import { ios, page } from "@/lib/content";
import { PageHead, Prose } from "@/lib/PageHead";

const info = page("/ios");

export const metadata: Metadata = { title: info.title };

export default function Ios() {
  return (
    <>
      <PageHead path="/ios" title={info.title} lede={info.lede} />
      <section className="docs-section">
        <p>
          <Prose text={ios.where} />
        </p>
        <p className="docs-muted">
          <Prose text={ios.differs} />
        </p>
      </section>
      {ios.sections.map((sec) => (
        <section key={sec.title} className="docs-section">
          <h2>{sec.title}</h2>
          <p className="docs-muted">
            <Prose text={sec.about} />
          </p>
          <CodeBlock code={sec.code} lang="swift" />
        </section>
      ))}
    </>
  );
}
