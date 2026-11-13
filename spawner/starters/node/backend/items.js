// The items API, on the items table (db/migrations/0001_items.sql): an example to copy for each
// thing the app keeps, or to change into the first one.
//
//   GET    /api/items         every item, oldest first
//   POST   /api/items         {title} → the new item (201)
//   PATCH  /api/items/:id     {title?, done?} → the item
//   DELETE /api/items/:id     (204)
const crypto = require("node:crypto");
const express = require("express");
const db = require("./db");

const router = express.Router();

router.get("/", async (req, res) => {
  res.json(await db.query("SELECT * FROM items ORDER BY created_at, id"));
});

router.post("/", async (req, res) => {
  const title = String(req.body?.title ?? "").trim();
  if (!title) return res.status(400).json({ error: "An item needs a title" });
  const id = crypto.randomUUID();
  await db.execute("INSERT INTO items (id, title) VALUES (?, ?)", [id, title]);
  res.status(201).json(await db.one("SELECT * FROM items WHERE id = ?", [id]));
});

router.patch("/:id", async (req, res) => {
  const item = await db.one("SELECT * FROM items WHERE id = ?", [req.params.id]);
  if (!item) return res.status(404).json({ error: "No such item" });
  const title = req.body?.title === undefined ? item.title : String(req.body.title).trim();
  if (!title) return res.status(400).json({ error: "An item needs a title" });
  const done = req.body?.done === undefined ? item.done : Boolean(req.body.done);
  await db.execute("UPDATE items SET title = ?, done = ? WHERE id = ?", [title, done, item.id]);
  res.json(await db.one("SELECT * FROM items WHERE id = ?", [item.id]));
});

router.delete("/:id", async (req, res) => {
  if (!(await db.execute("DELETE FROM items WHERE id = ?", [req.params.id]))) return res.status(404).json({ error: "No such item" });
  res.status(204).end();
});

module.exports = router;
