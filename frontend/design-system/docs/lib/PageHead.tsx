import Link from "next/link";
import type { ReactNode } from "react";
import { Code } from "@labb/ui";
import { markdownPath } from "./content";

// Text from content.ts, with `code` in backticks shown as code
export function Prose({ text }: { text: string }) {
  return (
    <>
      {text.split(/`([^`]+)`/).map((part, i) => (i % 2 ? <Code key={i}>{part}</Code> : part))}
    </>
  );
}

// A page's title, intro and a link to its Markdown, which agents can also find from <head>
export function PageHead({
  path,
  title,
  lede,
  back,
  children,
}: {
  path: string;
  title: ReactNode;
  lede: string;
  back?: { href: string; label: string };
  children?: ReactNode;
}) {
  // A plain link: the Markdown isn't a page Next can navigate to
  const md = `${process.env.PAGES_BASE_PATH ?? ""}${markdownPath(path)}`;
  return (
    <header className="docs-page-head">
      <link rel="alternate" type="text/markdown" href={md} />
      <div className="docs-page-top">
        {back ? (
          <Link href={back.href} className="docs-back">
            ← {back.label}
          </Link>
        ) : (
          <span />
        )}
        <a className="docs-md-link" href={md}>
          View as Markdown
        </a>
      </div>
      <h1>{title}</h1>
      <p className="docs-lede">
        <Prose text={lede} />
      </p>
      {children}
    </header>
  );
}
