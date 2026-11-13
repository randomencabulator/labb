# The app's server: backend/, run with Node on port 8080. It's built from the app's folder so it can
# copy db/, but it's only the server: the page (frontend/) is built and served apart, by itself.
# Packages go in backend/package.json's dependencies, with exact versions.
FROM node:24-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY backend/package.json ./
RUN npm install --omit=dev --no-audit --no-fund --loglevel=error
COPY backend/ ./
COPY db/ ./db/
EXPOSE 8080
CMD ["node", "--disable-warning=ExperimentalWarning", "server.js"]
