import { useState } from "react";
import { RegenerateIcon } from "./Icons";

const PUBLISHED = "Published at ";

/** Where a publish_artifact call put its page, from its result's first line */
export function publishedUrl(firstLine: string | undefined): string | null {
  if (!firstLine?.startsWith(PUBLISHED)) return null;
  const url = firstLine.slice(PUBLISHED.length).trim();
  return /^https?:\/\/[^\s]+\/artifacts\/[a-z0-9-]+\/$/.test(url) ? url : null;
}

/** A page's title from its name: "sales-chart-k3f9qa" is "Sales chart" */
function titleOf(name: string) {
  const words = name.replace(/-[a-z0-9]{6}$/, "").replace(/-/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A web page the model published (tools/artifacts.py), with the page itself under it. It's on
 * labb-sites, another port and so another origin: it keeps its own, and can't reach labb's. */
export function ArtifactCard({ url, name }: { url: string; name: string }) {
  const [reloads, setReloads] = useState(0);
  // labb's picked theme, which the page takes from ?theme= (without one it follows the system, as labb does)
  const theme = document.documentElement.dataset.theme;
  const shown = theme === "dark" || theme === "light" ? `${url}?theme=${theme}` : url;
  return (
    <figure className="artifact-card">
      <figcaption className="artifact-head">
        <span className="artifact-title">{titleOf(name)}</span>
        <button type="button" className="icon-button" aria-label="Reload the page" title="Reload" onClick={() => setReloads(n => n + 1)}>
          <RegenerateIcon />
        </button>
        <a className="artifact-open" href={url} target="_blank" rel="noopener noreferrer">
          Open ↗
        </a>
      </figcaption>
      <iframe
        key={reloads}
        className="artifact-frame"
        src={shown}
        title={titleOf(name)}
        loading="lazy"
        sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-forms allow-downloads"
      />
    </figure>
  );
}
