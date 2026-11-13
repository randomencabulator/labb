import path from "node:path";
import type { NextConfig } from "next";

// The packages are TypeScript source for now, so Next compiles them. The root is the repository's,
// where the npm workspace (web and the design system) keeps node_modules
const root = path.join(process.cwd(), "..", "..");

const config: NextConfig = {
  transpilePackages: ["@labb/tokens", "@labb/ui"],
  turbopack: { root },
  outputFileTracingRoot: root,
  // Plain HTML in out/, for any static host. Each page is a folder, so /colors/ is out/colors/index.html
  output: "export",
  trailingSlash: true,
  // Where the site lives on its host, e.g. /labb for randomencabulator.github.io/labb (set by the workflow)
  basePath: process.env.PAGES_BASE_PATH || undefined,
};

export default config;
