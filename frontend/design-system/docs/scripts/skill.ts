// Writes the Agent Skill (lib/skill.ts) to frontend/design-system/skills/<name>/, or with --check fails if
// what's there is out of date. Run with tsx: npm run skill (or npm run skill -- --check).
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { SKILL_NAME, skillFiles } from "../lib/skill";

const dir = path.join(import.meta.dirname, "..", "..", "skills", SKILL_NAME);
const files = skillFiles();
const check = process.argv.includes("--check");

// Every file in the folder now, so one the skill no longer has is caught too
const present = (d: string, base = ""): string[] =>
  existsSync(d) ? readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? present(path.join(d, e.name), `${base}${e.name}/`) : [`${base}${e.name}`])) : [];

const stale = [
  ...Object.entries(files).filter(([f, text]) => !existsSync(path.join(dir, f)) || readFileSync(path.join(dir, f), "utf8") !== text).map(([f]) => f),
  ...present(dir).filter((f) => !(f in files)),
];

if (check) {
  if (stale.length) {
    console.error(`skill: skills/${SKILL_NAME} is out of date (${stale.join(", ")}): run npm run skill`);
    process.exit(1);
  }
  console.log(`skill: skills/${SKILL_NAME} is up to date`);
} else {
  rmSync(dir, { recursive: true, force: true });
  for (const [f, text] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(dir, f)), { recursive: true });
    writeFileSync(path.join(dir, f), text);
  }
  console.log(`skill: wrote skills/${SKILL_NAME} (${Object.keys(files).join(", ")})`);
}
