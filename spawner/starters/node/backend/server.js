// The app's server: its JSON API, under /api/, on port 8080. The page (frontend/) isn't served here:
// it's built and served apart, and its calls to /api/… come here. See AGENTS.md.
//
// Each part of the API is a module like items.js (an express.Router), used below.
const express = require("express");
const db = require("./db");
const items = require("./items");

const app = express();
app.set("trust proxy", true);
app.use(express.json());

// Whether the server answers: labb asks after each build
app.get("/api/health", (req, res) => res.json({ ok: true }));
app.use("/api/items", items);

app.use("/api", (req, res) => res.status(404).json({ error: "Not found" }));
// A route that throws (or whose promise fails): written to the log, and said to the page
app.use((error, req, res, next) => {
  console.error(error);
  res.status(error.status || 500).json({ error: error.expose ? error.message : "Something went wrong on the server" });
});

const PORT = Number(process.env.PORT || 8080);
// The tables first (db/migrations/), then listening
db.migrate().then(() => {
  app.listen(PORT, "0.0.0.0", () => console.log(`${process.env.APP_NAME || "App"}'s server on port ${PORT}`));
});
