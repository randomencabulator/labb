import type { Metadata } from "next";
import { CodeBlock } from "@/lib/CodeBlock";
import { page, skillNotes } from "@/lib/content";
import { PageHead, Prose } from "@/lib/PageHead";
import { installSteps, SKILL_NAME, skillFiles, skillUrl } from "@/lib/skill";

const info = page("/skill");

export const metadata: Metadata = { title: info.title };

export default function Skill() {
  const files = Object.keys(skillFiles());
  return (
    <>
      <PageHead path="/skill" title={info.title} lede={info.lede} />
      <section className="docs-section">
        <p>
          <Prose text={skillNotes.what} />
        </p>
        <h2>What it teaches</h2>
        <ul className="docs-list">
          {skillNotes.teaches.map((t) => (
            <li key={t}>
              <Prose text={t} />
            </li>
          ))}
        </ul>
      </section>
      <section className="docs-section">
        <h2>Its files</h2>
        <ul className="docs-list">
          {files.map((f) => (
            <li key={f}>
              <a className="lb-link" href={skillUrl(f)}>
                {SKILL_NAME}/{f}
              </a>
            </li>
          ))}
        </ul>
        <p className="docs-muted">
          <Prose text={skillNotes.generated} />
        </p>
      </section>
      {installSteps().map((step) => (
        <section key={step.title} className="docs-section">
          <h2>{step.title}</h2>
          <p className="docs-muted">{step.about}</p>
          <CodeBlock code={step.code} lang={step.lang} />
        </section>
      ))}
    </>
  );
}
