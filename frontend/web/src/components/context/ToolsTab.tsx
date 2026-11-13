import { useState } from "react";
import type { ContextInspection } from "../../api";
import { Section, percent } from "./Breakdown";

/** The tools the model is offered, as it's told about them */
export function ToolsTab({ seen }: { seen: ContextInspection }) {
  const [query, setQuery] = useState("");
  const words = query.trim().toLowerCase();
  const total = seen.parts.find(p => p.kind === "tools")?.tokens ?? 0;
  const shown = [...seen.tools]
    .filter(tool => !words || tool.name.toLowerCase().includes(words) || tool.description.toLowerCase().includes(words))
    .sort((a, b) => b.tokens - a.tokens);

  if (seen.tools.length === 0) return <p className="muted">The next model call isn't offered any tools.</p>;
  return (
    <>
      <div className="context-filters">
        <input type="search" placeholder="Search the tools" aria-label="Search the tools" value={query} onChange={e => setQuery(e.target.value)} />
      </div>
      <p className="muted context-count">
        {seen.tools.length} tool{seen.tools.length === 1 ? "" : "s"} · {total.toLocaleString()} tokens together ({percent(total, seen.window)} of the window)
      </p>
      {shown.map(tool => (
        <Section key={tool.name} title={<code>{tool.name}</code>} tokens={tool.tokens}>
          {tool.description && <pre>{tool.description}</pre>}
          <pre className="muted">{JSON.stringify(tool.parameters, null, 2)}</pre>
        </Section>
      ))}
      {shown.length === 0 && <p className="muted">Nothing matches.</p>}
    </>
  );
}
