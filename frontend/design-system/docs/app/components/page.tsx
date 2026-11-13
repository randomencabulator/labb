import type { Metadata } from "next";
import Link from "next/link";
import { components } from "@/lib/registry";
import { Preview } from "@/lib/demos";
import { page } from "@/lib/content";
import { PageHead } from "@/lib/PageHead";

const info = page("/components");

export const metadata: Metadata = { title: info.title };

export default function Components() {
  return (
    <>
      <PageHead path="/components" title={info.title} lede={info.lede} />
      <div className="component-index">
        {components.map((c) => (
          <Link key={c.slug} href={`/components/${c.slug}`} className="component-tile">
            <div className="component-tile-preview" inert>
              <Preview slug={c.slug} />
            </div>
            <div className="component-tile-text">
              <b>{c.name}</b>
              <span className="docs-muted docs-small">{c.about}</span>
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
