#!/bin/sh
# An artifact's build: its files (/src, read-only) in place of the template's, then `next build`, and
# the static site copied to /out. node_modules and Next's cache stay the image's.
set -e
cd /template
find . -mindepth 1 -maxdepth 1 ! -name node_modules ! -name .next -exec rm -rf {} +
cp -R /src/. .
npx next build
rm -rf /out/* 2>/dev/null || true
cp -R out/. /out/
