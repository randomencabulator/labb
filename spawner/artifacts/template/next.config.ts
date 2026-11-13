import type { NextConfig } from "next";

// A static site: `next build` writes it to out/, which is what's published. The builder sets
// ARTIFACT_BASE_PATH to where it's served (/artifacts/<name>), so links and assets resolve there.
const config: NextConfig = {
  output: "export",
  basePath: process.env.ARTIFACT_BASE_PATH || undefined,
  trailingSlash: true,
  images: { unoptimized: true },
};

export default config;
