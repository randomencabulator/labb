// The agent skill's files, at /skills/<name>/SKILL.md and /skills/<name>/references/…
import { SKILL_NAME, skillFiles } from "@/lib/skill";

export const dynamic = "force-static";
export const dynamicParams = false;

export function generateStaticParams() {
  return Object.keys(skillFiles()).map((f) => ({ file: [SKILL_NAME, ...f.split("/")] }));
}

export async function GET(_: Request, { params }: { params: Promise<{ file: string[] }> }) {
  const [name, ...rest] = (await params).file;
  const text = name === SKILL_NAME ? skillFiles()[rest.join("/")] : undefined;
  if (text == null) return new Response("Not found\n", { status: 404 });
  return new Response(text, { headers: { "Content-Type": "text/markdown; charset=utf-8" } });
}
