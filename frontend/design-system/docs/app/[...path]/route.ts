// Every page as Markdown at its path plus .md (/colors.md, /components/button.md, /index.md),
// written out as files by the static export.
import { existsSync } from "node:fs";
import path from "node:path";
import { markdownPages } from "@/lib/markdown";
import { components } from "@/lib/registry";

export const dynamic = "force-static";
export const dynamicParams = false;

export function generateStaticParams() {
  // Every component in the registry needs app/components/<slug>/page.tsx (a dynamic [slug] would
  // catch /components/<slug>.md before this route does)
  const missing = components.filter((c) => !existsSync(path.join(process.cwd(), "app/components", c.slug, "page.tsx")));
  if (missing.length) throw new Error(`No page for ${missing.map((c) => `app/components/${c.slug}/page.tsx`).join(", ")}`);
  return Object.keys(markdownPages()).map((md) => ({ path: md.slice(1).split("/") }));
}

export async function GET(_: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const markdown = markdownPages()[`/${path.join("/")}`];
  if (markdown == null) return new Response("Not found\n", { status: 404 });
  return new Response(markdown, { headers: { "Content-Type": "text/markdown; charset=utf-8" } });
}
