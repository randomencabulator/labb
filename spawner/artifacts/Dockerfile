# labb-artifact-builder: builds an artifact's page (the spawner runs it, with no network). The template
# and its node_modules are installed here once, and built once so Next's cache starts warm.
#
#   docker run --rm --network none -v <src>:/src:ro -v <out>:/out -e ARTIFACT_BASE_PATH=/artifacts/<name> labb-artifact-builder
FROM node:24-alpine

ENV NEXT_TELEMETRY_DISABLED=1 \
    npm_config_update_notifier=false \
    npm_config_fund=false \
    npm_config_audit=false

WORKDIR /template
RUN chown node:node /template
USER node
COPY --chown=node:node template/package.json template/package-lock.json ./
RUN npm ci --loglevel=error && npm cache clean --force
COPY --chown=node:node template/ ./
RUN npm run build && rm -rf out

COPY --chown=node:node build.sh /usr/local/bin/build-artifact
CMD ["build-artifact"]
