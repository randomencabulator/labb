import { CopyButton } from "./Copy";
import { highlight, type Lang } from "./highlight";

export { CopyValue } from "./Copy";

// A highlighted code block with its language and a copy button; highlighted when the page is built
export async function CodeBlock({ code, lang = "tsx" }: { code: string; lang?: Lang }) {
  const html = await highlight(code, lang);
  return (
    <div className="docs-code">
      <div className="docs-code-head">
        <span>{lang}</span>
        <CopyButton text={code} />
      </div>
      <div className="docs-code-body" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
