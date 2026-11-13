import { type ComponentProps, type ReactElement, Children, isValidElement, memo, useDeferredValue, useRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { CopyButton } from "./CopyButton";

// A code block with its language and a Copy button
function CodeBlock({ children, node: _, ...props }: ComponentProps<"pre"> & { node?: unknown }) {
  const pre = useRef<HTMLPreElement>(null);
  const code = Children.toArray(children)[0];
  const className = isValidElement(code) ? ((code as ReactElement<{ className?: string }>).props.className ?? "") : "";
  const language = /language-([\w+#-]+)/.exec(className)?.[1];

  return (
    <div className="code">
      <div className="code-head">
        <span>{language ?? "code"}</span>
        <CopyButton getText={() => pre.current?.textContent ?? ""} />
      </div>
      <pre ref={pre} {...props}>
        {children}
      </pre>
    </div>
  );
}

// A clip a Studio Speak step made (/speech/clips/<id>), on this server
const CLIP = /^(?:https?:\/\/[^/]+)?\/speech\/clips\/[0-9a-f]{32}$/;

const components: Components = {
  pre: CodeBlock,
  // Links in replies open in a new tab; a spoken clip plays here
  a: ({ node: _, ...props }) =>
    props.href && CLIP.test(props.href) && (!/^https?:/.test(props.href) || new URL(props.href).origin === location.origin) ? (
      <audio className="clip" controls preload="none" src={props.href} title={String(props.children ?? "")} />
    ) : (
      <a {...props} target="_blank" rel="noopener noreferrer" />
    ),
};

const remarkPlugins = [remarkGfm, remarkBreaks];
const rehypePlugins = [[rehypeHighlight, { detect: true }] as const] as ComponentProps<typeof ReactMarkdown>["rehypePlugins"];

/** A reply rendered as Markdown. Raw HTML in it is shown as text, not rendered. */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  // While a reply streams, re-render the Markdown when there's time rather than on every token
  const deferred = useDeferredValue(text);
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}>
        {deferred}
      </ReactMarkdown>
    </div>
  );
});
