// Builds the tokens from tokens/*.json (Design Tokens Format 2025.10, with a resolver document for
// light and dark) into generated/ (tokens.css and tokens.ts) and the Swift package in ../../swift
// (swift.ts). Fails on anything the format doesn't allow, a reference that doesn't resolve, light
// and dark that don't match, or a colour below the contrast its token asks for.
//
//   node --experimental-strip-types scripts/build.ts            write generated/
//   node --experimental-strip-types scripts/build.ts --check    fail if generated/ is out of date
//   node --experimental-strip-types scripts/build.ts --watch    rebuild when a token file changes
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, watch, writeFileSync } from "node:fs";
import path from "node:path";
import { contrast } from "../src/contrast.ts";
import { SWIFT_GENERATED, swiftFiles, type SwiftColor, type SwiftInput } from "./swift.ts";

const ROOT = path.join(import.meta.dirname, "..");
const TOKENS = path.join(ROOT, "tokens");
const OUT = path.join(ROOT, "generated");
// The Swift package's target, where swift.ts's files go (only into SWIFT_GENERATED's folders)
const SWIFT = path.join(ROOT, "..", "..", "swift", "Sources", "LabbDesign");
const X = "dev.labb"; // our key in $extensions

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
type Obj = { [k: string]: Json };
type Token = { path: string; type: string; value: Json; description?: string; ext: Obj };

class TokenError extends Error {}
const fail = (msg: string): never => {
  throw new TokenError(msg);
};
const isObj = (v: Json | undefined): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const read = (file: string): Obj => {
  const data = JSON.parse(readFileSync(path.join(TOKENS, file), "utf8")) as Json;
  if (!isObj(data)) fail(`${file}: not a JSON object`);
  return data as Obj;
};

// ---------- reading: the resolver, then each file's tokens ----------

const TYPES = ["color", "dimension", "fontFamily", "fontWeight", "number", "duration", "cubicBezier", "shadow", "transition"];
const TOKEN_KEYS = new Set(["$value", "$type", "$description", "$extensions", "$deprecated"]);
const GROUP_KEYS = new Set(["$type", "$description", "$extensions", "$deprecated", "$schema"]);

function collect(node: Obj, file: string, at: string[], inherited: string | undefined, into: Map<string, Token>) {
  const type = (node.$type as string | undefined) ?? inherited;
  if ("$value" in node) {
    for (const k of Object.keys(node)) if (!TOKEN_KEYS.has(k)) fail(`${file}: ${at.join(".")} has "${k}", which a token can't have`);
    if (!type || !TYPES.includes(type)) fail(`${file}: ${at.join(".")} has no known $type (${type ?? "none"})`);
    const key = at.join(".");
    if (into.has(key)) fail(`${file}: ${key} is defined twice`);
    const ext = isObj(node.$extensions) && isObj(node.$extensions[X]) ? (node.$extensions[X] as Obj) : {};
    into.set(key, { path: key, type: type!, value: node.$value, description: node.$description as string | undefined, ext });
    return;
  }
  for (const [k, v] of Object.entries(node)) {
    if (k.startsWith("$")) {
      if (!GROUP_KEYS.has(k)) fail(`${file}: ${at.join(".") || "(top)"} has "${k}", which a group can't have`);
      continue;
    }
    if (/[{}.]/.test(k)) fail(`${file}: "${k}" can't be a name: names can't have { } or .`);
    if (!isObj(v)) fail(`${file}: ${[...at, k].join(".")} is neither a token nor a group`);
    collect(v as Obj, file, [...at, k], type, into);
  }
}

// Group metadata (title, description), for the docs
function groupsOf(node: Obj, at: string[] = [], into = new Map<string, { title?: string; about?: string }>()) {
  for (const [k, v] of Object.entries(node)) {
    if (k.startsWith("$") || !isObj(v) || "$value" in v) continue;
    const ext = isObj(v.$extensions) && isObj(v.$extensions[X]) ? (v.$extensions[X] as Obj) : {};
    into.set([...at, k].join("."), { title: ext.title as string | undefined, about: v.$description as string | undefined });
    groupsOf(v, [...at, k], into);
  }
  return into;
}

type Resolved = { base: Map<string, Token>; themes: Record<"light" | "dark", Map<string, Token>>; groups: Map<string, { title?: string; about?: string }> };

function load(): Resolved {
  const resolver = read("labb.resolver.json");
  if (resolver.version !== "2025.10") fail(`labb.resolver.json: version must be "2025.10"`);
  const sets = resolver.sets as Obj;
  const modifiers = resolver.modifiers as Obj;
  const sourcesOf = (list: Json): string[] => (list as Obj[]).map((s) => s.$ref as string);
  const base = new Map<string, Token>();
  const groups = new Map<string, { title?: string; about?: string }>();
  let theme: Obj | undefined;
  for (const step of resolver.resolutionOrder as Obj[]) {
    const ref = step.$ref as string;
    if (ref.startsWith("#/sets/")) {
      for (const file of sourcesOf((sets[ref.slice(7)] as Obj).sources)) {
        const data = read(file);
        collect(data, file, [], undefined, base);
        groupsOf(data, [], groups);
      }
    } else if (ref.startsWith("#/modifiers/")) {
      theme = modifiers[ref.slice(12)] as Obj;
    } else fail(`labb.resolver.json: can't follow ${ref}`);
  }
  if (!theme) fail("labb.resolver.json: no theme modifier");
  const contexts = theme!.contexts as Obj;
  if (!contexts.light || !contexts.dark || theme!.default !== "light") fail("labb.resolver.json: the theme needs light (the default) and dark");
  const themes = { light: new Map<string, Token>(), dark: new Map<string, Token>() };
  for (const name of ["light", "dark"] as const) {
    for (const file of sourcesOf(contexts[name])) {
      const data = read(file);
      collect(data, file, [], undefined, themes[name]);
      if (name === "light") groupsOf(data, [], groups);
    }
    for (const key of themes[name].keys()) if (base.has(key)) fail(`${key} is in both a set and the ${name} theme`);
  }
  const l = [...themes.light.keys()];
  const d = [...themes.dark.keys()];
  const missing = [...l.filter((k) => !themes.dark.has(k)).map((k) => `${k} (not in dark)`), ...d.filter((k) => !themes.light.has(k)).map((k) => `${k} (not in light)`)];
  if (missing.length) fail(`light and dark must have the same tokens: ${missing.join(", ")}`);
  if (l.join() !== d.join()) fail("light and dark must list their tokens in the same order");
  return { base, themes, groups };
}

// ---------- checking values and resolving references ----------

const REF = /^\{([^{}]+)\}$/;
const refOf = (v: Json) => (typeof v === "string" && REF.test(v) ? v.slice(1, -1) : null);

type Color = { components: [number, number, number]; alpha: number; hex?: string };

function checkValue(t: Token, type: string, v: Json, where: string, all: Map<string, Token>): void {
  const target = refOf(v);
  if (target) {
    const to = all.get(target) ?? fail(`${where}: ${v} doesn't resolve`);
    if (to.type !== type) fail(`${where}: ${v} is a ${to.type}, not a ${type}`);
    return;
  }
  const o = v as Obj;
  const num = (x: Json | undefined, what: string) => (typeof x === "number" && Number.isFinite(x) ? x : fail(`${where}: ${what} must be a number`));
  switch (type) {
    case "color": {
      if (!isObj(v) || o.colorSpace !== "srgb") fail(`${where}: a colour is {colorSpace: "srgb", components: [r, g, b], alpha?, hex?}`);
      const c = o.components as Json[];
      if (!Array.isArray(c) || c.length !== 3 || c.some((x) => typeof x !== "number" || x < 0 || x > 1)) fail(`${where}: components are three numbers from 0 to 1`);
      if (o.alpha !== undefined && (typeof o.alpha !== "number" || o.alpha < 0 || o.alpha > 1)) fail(`${where}: alpha is from 0 to 1`);
      if (o.hex !== undefined) {
        const hex = String(o.hex).toLowerCase();
        const want = hexOf({ components: c as [number, number, number], alpha: (o.alpha as number) ?? 1 });
        if (!/^#[0-9a-f]{6}([0-9a-f]{2})?$/.test(hex)) fail(`${where}: hex must be #rrggbb or #rrggbbaa`);
        if (hex !== want) {
          const comps = [1, 3, 5].map((i) => Math.round((parseInt(hex.slice(i, i + 2), 16) / 255) * 1e4) / 1e4);
          fail(`${where}: hex ${hex} doesn't match components (${want}); for ${hex}, components are [${comps.join(", ")}]`);
        }
      }
      return;
    }
    case "dimension":
      num(o?.value, "a dimension's value");
      if (o.unit !== "px" && o.unit !== "rem") fail(`${where}: a dimension's unit is px or rem`);
      return;
    case "duration":
      num(o?.value, "a duration's value");
      if (o.unit !== "ms" && o.unit !== "s") fail(`${where}: a duration's unit is ms or s`);
      return;
    case "number":
      num(v, "it");
      return;
    case "fontWeight":
      if (typeof v === "number" ? v < 1 || v > 1000 : typeof v !== "string") fail(`${where}: a font weight is 1 to 1000`);
      return;
    case "fontFamily":
      if (!(typeof v === "string" || (Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === "string")))) fail(`${where}: a font family is a name or a list of names`);
      return;
    case "cubicBezier":
      if (!Array.isArray(v) || v.length !== 4 || v.some((x) => typeof x !== "number") || [v[0], v[2]].some((x) => (x as number) < 0 || (x as number) > 1))
        fail(`${where}: a cubic Bézier is [x1, y1, x2, y2] with x from 0 to 1`);
      return;
    case "shadow":
      if (!isObj(v)) fail(`${where}: a shadow is {color, offsetX, offsetY, blur, spread}`);
      for (const k of Object.keys(o)) if (!["color", "offsetX", "offsetY", "blur", "spread", "inset"].includes(k)) fail(`${where}: a shadow can't have "${k}"`);
      checkValue(t, "color", o.color, `${where}.color`, all);
      for (const k of ["offsetX", "offsetY", "blur", "spread"]) checkValue(t, "dimension", o[k], `${where}.${k}`, all);
      return;
    case "transition":
      if (!isObj(v)) fail(`${where}: a transition is {duration, delay, timingFunction}`);
      checkValue(t, "duration", o.duration, `${where}.duration`, all);
      checkValue(t, "duration", o.delay, `${where}.delay`, all);
      checkValue(t, "cubicBezier", o.timingFunction, `${where}.timingFunction`, all);
      return;
  }
}

// Follows references to a value, and says which palette step a colour came from
function resolve(v: Json, all: Map<string, Token>, seen: string[] = []): { value: Json; via: string[] } {
  const target = refOf(v);
  if (!target) return { value: v, via: seen };
  if (seen.includes(target)) fail(`a reference loop: ${[...seen, target].join(" → ")}`);
  return resolve(all.get(target)!.value, all, [...seen, target]);
}

// ---------- CSS values ----------

const hexOf = ({ components, alpha }: { components: number[]; alpha: number }) =>
  "#" + [...components, ...(alpha < 1 ? [alpha] : [])].map((c) => Math.round(c * 255).toString(16).padStart(2, "0")).join("");

function cssColor(v: Json): string {
  const c = v as unknown as Color;
  const alpha = c.alpha ?? 1;
  if (alpha === 0) return "transparent";
  if (alpha < 1 && !c.hex) return `rgb(${c.components.map((x) => Math.round(x * 255)).join(" ")} / ${alpha})`;
  return hexOf({ components: c.components, alpha });
}

const ms = (d: Obj) => (d.unit === "s" ? (d.value as number) * 1000 : (d.value as number));
const px = (d: Obj) => `${d.value}${d.unit}`;
const EASE = [0.25, 0.1, 0.25, 1];

const varName = (p: string) => {
  const parts = p.split(".");
  const name = parts[parts.length - 1];
  switch (parts[0]) {
    case "ref":
      return `--lb-ref-${parts.slice(1).join("-")}`;
    case "sys":
      if (parts[1] === "color") return `--lb-sys-color-${name}`;
      return fail(`no CSS name for ${p}`);
    case "brand":
      return `--lb-brand-${name}`;
    case "font":
      return { family: `--lb-font-${name}`, size: `--lb-text-${name}`, lineHeight: `--lb-leading-${name}`, weight: `--lb-weight-${name}`, tracking: `--lb-tracking-${name}` }[parts[1]] ?? fail(`no CSS name for ${p}`);
    default:
      if (parts[0] === "sys") return fail(`no CSS name for ${p}`);
      return `--lb-${parts.join("-")}`;
  }
};

// A token's value in CSS: references to colours stay references (var()), so themes switch them
function css(t: Token, v: Json, all: Map<string, Token>): string {
  const target = refOf(v);
  if (target && t.type === "color") return `var(${varName(target)})`;
  if (target) return css(t, resolve(v, all).value, all);
  const o = v as Obj;
  switch (t.type) {
    case "color":
      return cssColor(v);
    case "dimension":
      return px(o);
    case "number":
      return t.path.startsWith("font.tracking.") ? `${v}em` : String(v);
    case "fontWeight":
      return String(v);
    case "fontFamily":
      return (Array.isArray(v) ? v : [v]).map((f) => (/\s/.test(String(f)) ? `"${f}"` : String(f))).join(", ");
    case "shadow": {
      const part = (k: string, type: string) => css({ ...t, type }, o[k], all);
      return `${t.ext.inset ? "inset " : ""}${part("offsetX", "dimension")} ${part("offsetY", "dimension")} ${part("blur", "dimension")} ${part("spread", "dimension")} ${part("color", "color")}`;
    }
    case "transition": {
      const d = resolve(o.duration, all).value as Obj;
      const curve = resolve(o.timingFunction, all).value as number[];
      const delay = ms(resolve(o.delay, all).value as Obj);
      const easing = curve.join() === EASE.join() ? "ease" : `cubic-bezier(${curve.join(", ")})`;
      return `${ms(d)}ms ${easing}${delay ? ` ${delay}ms` : ""}`;
    }
  }
  return fail(`no CSS for ${t.path}`);
}

// ---------- building ----------

function build(): { css: string; ts: string; swift: Record<string, string> } {
  const { base, themes, groups } = load();
  const light = new Map([...base, ...themes.light]);
  const dark = new Map([...base, ...themes.dark]);
  for (const [name, all] of [["light", light], ["dark", dark]] as const) {
    for (const t of all.values()) checkValue(t, t.type, t.value, `${t.path}${themes[name].has(t.path) ? ` (${name})` : ""}`, all);
    for (const t of all.values()) resolve(t.value, all);
  }

  // System colour names become CSS names, so they must be unique across their groups
  const systemColors = [...themes.light.values()].filter((t) => t.path.startsWith("sys.color."));
  if (!systemColors.length) fail("no system colours: the theme files must have sys.color.<group>.<name> tokens");
  const names = new Map<string, string>();
  for (const t of systemColors) {
    const n = t.path.split(".").pop()!;
    if (names.has(n)) fail(`${t.path} and ${names.get(n)} would both be --lb-sys-color-${n}`);
    names.set(n, t.path);
    if (!t.description) fail(`${t.path}: say what it's for in $description (sys/light.json)`);
  }

  // Contrast the tokens ask for, in both themes
  const resolvedHex = (p: string, all: Map<string, Token>) => cssColor(resolve(all.get(p)!.value, all).value);
  const problems: string[] = [];
  for (const t of systemColors) {
    const want = t.ext.contrast as Obj | undefined;
    if (!want?.min) continue;
    const on = names.get(want.on as string) ?? fail(`${t.path}: contrast on "${want.on}", which isn't a colour`);
    for (const [theme, all] of [["light", light], ["dark", dark]] as const) {
      const ratio = contrast(resolvedHex(t.path, all), resolvedHex(on, all));
      if (ratio == null || ratio < (want.min as number)) problems.push(`${t.path} on ${want.on} in ${theme}: ${ratio?.toFixed(2) ?? "?"}:1, needs ${want.min}:1`);
    }
  }
  if (problems.length) fail(`Not enough contrast:\n  ${problems.join("\n  ")}`);

  // ----- CSS -----
  const decl = (t: Token, all: Map<string, Token>) => `  ${varName(t.path)}: ${css(t, t.value, all)};`;
  const shared = [...base.values()].map((t) => decl(t, light)).join("\n");
  const lightColors = [...themes.light.values()].map((t) => decl(t, light)).join("\n");
  const darkColors = [...themes.dark.values()].map((t) => decl(t, dark)).join("\n");
  const indent = (s: string) => s.replace(/^/gm, "  ");
  const cssText = [
    "/* Generated by packages/tokens/scripts/build.ts from tokens/*.json. Don't edit; edit the JSON. */",
    `:root {\n${shared}\n${lightColors}\n}`,
    `@media (prefers-color-scheme: dark) {\n  :root:not([data-theme="light"]) {\n${indent(darkColors)}\n    color-scheme: dark;\n  }\n}`,
    `:root[data-theme="dark"] {\n${darkColors}\n  color-scheme: dark;\n}`,
    "/* A part of the page in one theme, whatever the page is in */",
    `[data-lb-theme="light"] {\n${lightColors}\n  color-scheme: light;\n}`,
    `[data-lb-theme="dark"] {\n${darkColors}\n  color-scheme: dark;\n}`,
  ].join("\n\n") + "\n";

  // ----- TypeScript -----
  const tokensUnder = (prefix: string) => [...base.values()].filter((t) => t.path.startsWith(prefix + "."));
  const leaf = (t: Token) => t.path.split(".").pop()!;
  const fromRef = (v: Json) => refOf(v)?.replace(/^ref\./, "");
  const stepOf = (v: Json, all: Map<string, Token>) => {
    const r = resolve(v, all);
    const step = r.via.find((p) => p.startsWith("ref."));
    return step?.slice(4);
  };

  const palette = [...groups.entries()]
    .filter(([p]) => /^ref\.[^.]+$/.test(p))
    .map(([p, g]) => ({
      name: p.slice(4),
      title: g.title ?? p.slice(4),
      about: g.about ?? "",
      steps: tokensUnder(p).map((t) => ({ step: leaf(t), value: cssColor(t.value), pinned: t.ext.pinned === true })),
    }));
  const refSingles = tokensUnder("ref")
    .filter((t) => t.path.split(".").length === 2)
    .map((t) => ({ name: leaf(t), value: cssColor(t.value), about: t.description ?? "" }));

  const colors = [...groups.entries()]
    .filter(([p]) => /^sys\.color\.[^.]+$/.test(p))
    .map(([p, g]) => ({
      name: g.title ?? p.slice(10),
      about: g.about ?? "",
      tokens: [...themes.light.values()]
        .filter((t) => t.path.startsWith(p + "."))
        .map((t) => {
          const d = themes.dark.get(t.path)!;
          return {
            name: leaf(t),
            light: resolvedHex(t.path, light),
            dark: resolvedHex(t.path, dark),
            ...(stepOf(t.value, light) ? { lightRef: stepOf(t.value, light) } : {}),
            ...(stepOf(d.value, dark) ? { darkRef: stepOf(d.value, dark) } : {}),
            use: t.description!,
            ...(t.ext.web ? { web: t.ext.web as string } : {}),
            ...(t.ext.contrast ? { contrast: t.ext.contrast } : {}),
          };
        }),
    }));

  const value = (t: Token) => resolve(t.value, light).value as Obj;
  const data = {
    palette,
    refColors: refSingles,
    colors,
    brand: Object.fromEntries(tokensUnder("brand").map((t) => [leaf(t), cssColor(resolve(t.value, light).value)])),
    brandRefs: Object.fromEntries(tokensUnder("brand").map((t) => [leaf(t), fromRef(t.value) ?? ""])),
    space: Object.fromEntries(tokensUnder("space").map((t) => [leaf(t), value(t).value])),
    radius: Object.fromEntries(tokensUnder("radius").map((t) => [leaf(t), { value: px(value(t)), use: t.description ?? "" }])),
    font: {
      family: Object.fromEntries(tokensUnder("font.family").map((t) => [leaf(t), css(t, t.value, light)])),
      size: Object.fromEntries(
        tokensUnder("font.size").map((t) => [
          leaf(t),
          { size: value(t).value, line: resolve(base.get(`font.lineHeight.${leaf(t)}`)?.value ?? fail(`font.size.${leaf(t)} has no font.lineHeight.${leaf(t)}`), light).value, use: t.description ?? "" },
        ]),
      ),
      weight: Object.fromEntries(tokensUnder("font.weight").map((t) => [leaf(t), resolve(t.value, light).value])),
      tracking: Object.fromEntries(tokensUnder("font.tracking").map((t) => [leaf(t), css(t, t.value, light)])),
    },
    shadow: Object.fromEntries(tokensUnder("shadow").map((t) => [leaf(t), { value: css(t, t.value, light), use: t.description ?? "" }])),
    motion: Object.fromEntries(tokensUnder("motion").map((t) => [leaf(t), { value: css(t, t.value, light), use: t.description ?? "" }])),
    border: Object.fromEntries(tokensUnder("border").map((t) => [leaf(t), px(value(t))])),
    z: Object.fromEntries(tokensUnder("z").map((t) => [leaf(t), resolve(t.value, light).value])),
  };

  const lit = (v: unknown) => JSON.stringify(v, null, 2);
  const ts = `// Generated by packages/tokens/scripts/build.ts from tokens/*.json. Don't edit; edit the JSON.

export type Theme = "light" | "dark";

// A step of the reference palette: its CSS colour, and whether labb's own colours fixed it (pinned)
export type PaletteStep = { step: string; value: string; pinned: boolean };
export type Ramp = { name: string; title: string; about: string; steps: PaletteStep[] };

// A system colour: its value in each theme, the palette step it points at, what it's for, the
// variable in frontend/web/src/styles.css, and the colour it must stay readable on
export type ColorToken = {
  name: string;
  light: string;
  dark: string;
  lightRef?: string;
  darkRef?: string;
  use: string;
  web?: string;
  contrast?: { on: string; min?: number };
};
export type ColorGroup = { name: string; about: string; tokens: ColorToken[] };

// Reference colours: the palette, a ramp per hue, plus white and black
export const palette: Ramp[] = ${lit(data.palette)};
export const refColors: { name: string; value: string; about: string }[] = ${lit(data.refColors)};

// System colours: what each colour is for, in light and dark
export const colors: ColorGroup[] = ${lit(data.colors)};

// The fixed brand colours, and the palette steps they are
export const brand = ${lit(data.brand)} as const;
export const brandRefs = ${lit(data.brandRefs)} as const;

// Spacing in px
export const space: Record<string, number> = ${lit(data.space)};

export const radius: Record<string, { value: string; use: string }> = ${lit(data.radius)};

export const font: {
  family: Record<string, string>;
  size: Record<string, { size: number; line: number; use: string }>;
  weight: Record<string, number>;
  tracking: Record<string, string>;
} = ${lit(data.font)};

export const shadow: Record<string, { value: string; use: string }> = ${lit(data.shadow)};

export const motion: Record<string, { value: string; use: string }> = ${lit(data.motion)};

export const border: Record<string, string> = ${lit(data.border)};

export const z: Record<string, number> = ${lit(data.z)};

// Everything above as CSS variables, light and dark (also in tokens.css)
export const cssText = ${JSON.stringify(cssText)};
`;
  // ----- Swift -----
  const colorOf = (v: Json, all: Map<string, Token>) => {
    const c = resolve(v, all).value as unknown as Color;
    return { components: c.components, alpha: c.alpha ?? 1 } as SwiftColor;
  };
  const dim = (v: Json) => (resolve(v, light).value as Obj).value as number;
  const swiftInput: SwiftInput = {
    system: systemColors.map((t) => ({ name: leaf(t), use: t.description!, light: colorOf(t.value, light), dark: colorOf(themes.dark.get(t.path)!.value, dark) })),
    palette: tokensUnder("ref").map((t) => ({ name: t.path.slice(4).replace(/\./g, "-"), color: colorOf(t.value, light) })),
    brand: tokensUnder("brand").map((t) => ({ name: leaf(t), ref: (fromRef(t.value) ?? fail(`${t.path} must point at a palette step`)).replace(/\./g, "-") })),
    space: tokensUnder("space").map((t) => ({ key: leaf(t), px: dim(t.value) })),
    radius: tokensUnder("radius").map((t) => ({ key: leaf(t), px: dim(t.value), use: t.description ?? "" })),
    text: tokensUnder("font.size").map((t) => ({ key: leaf(t), size: dim(t.value), line: resolve(base.get(`font.lineHeight.${leaf(t)}`)!.value, light).value as number, use: t.description ?? "" })),
    weight: tokensUnder("font.weight").map((t) => ({ key: leaf(t), value: resolve(t.value, light).value as number })),
    tracking: tokensUnder("font.tracking").map((t) => ({ key: leaf(t), em: resolve(t.value, light).value as number, use: t.description ?? "" })),
    family: tokensUnder("font.family").map((t) => {
      const v = resolve(t.value, light).value;
      return { key: leaf(t), names: (Array.isArray(v) ? v : [v]) as string[] };
    }),
    shadow: tokensUnder("shadow").map((t) => {
      const o = resolve(t.value, light).value as Obj;
      const target = refOf(o.color);
      return {
        key: leaf(t),
        x: dim(o.offsetX),
        y: dim(o.offsetY),
        blur: dim(o.blur),
        spread: dim(o.spread),
        color: target?.startsWith("sys.color.") ? { system: target.split(".").pop()! } : { literal: colorOf(o.color, light) },
        inset: t.ext.inset === true,
        use: t.description ?? "",
      };
    }),
    motion: tokensUnder("motion").map((t) => {
      const o = resolve(t.value, light).value as Obj;
      return { key: leaf(t), ms: ms(resolve(o.duration, light).value as Obj), curve: resolve(o.timingFunction, light).value as number[], use: t.description ?? "" };
    }),
    border: tokensUnder("border").map((t) => ({ key: leaf(t), px: dim(t.value) })),
    z: tokensUnder("z").map((t) => ({ key: leaf(t), value: resolve(t.value, light).value as number })),
  };

  return { css: cssText, ts, swift: swiftFiles(swiftInput) };
}

// ---------- writing ----------

// Every file now in the generated Swift folders, so one the build no longer makes is caught too
function swiftPresent(dir = SWIFT, base = ""): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? swiftPresent(path.join(dir, e.name), `${base}${e.name}/`) : [`${base}${e.name}`],
  );
}

function run(check: boolean) {
  const out = build();
  const files: [string, string][] = [
    [path.join(OUT, "tokens.css"), out.css],
    [path.join(OUT, "tokens.ts"), out.ts],
    ...Object.entries(out.swift).map(([f, text]) => [path.join(SWIFT, f), text] as [string, string]),
  ];
  const ours = (f: string) => SWIFT_GENERATED.some((g) => f === g || f.startsWith(g + "/"));
  const extra = swiftPresent().filter((f) => ours(f) && !(f in out.swift)).map((f) => path.join(SWIFT, f));
  const show = (f: string) => path.relative(ROOT, f);
  if (check) {
    const stale = [...files.filter(([f, text]) => !existsSync(f) || readFileSync(f, "utf8") !== text).map(([f]) => f), ...extra].map(show);
    if (stale.length) fail(`generated files are out of date (${stale.slice(0, 5).join(", ")}${stale.length > 5 ? ", …" : ""}): run npm run tokens`);
    console.log("tokens: generated/ and the Swift package are up to date");
    return;
  }
  for (const f of extra) rmSync(f);
  for (const [f, text] of files) {
    mkdirSync(path.dirname(f), { recursive: true });
    if (!existsSync(f) || readFileSync(f, "utf8") !== text) writeFileSync(f, text);
  }
  console.log(`tokens: wrote generated/tokens.css, generated/tokens.ts and ${Object.keys(out.swift).length} files in the Swift package`);
}

const args = process.argv.slice(2);
try {
  run(args.includes("--check"));
} catch (e) {
  if (!(e instanceof TokenError)) throw e;
  console.error(`tokens: ${e.message}`);
  if (!args.includes("--watch")) process.exit(1);
}
if (args.includes("--watch")) {
  console.log("tokens: watching tokens/");
  let timer: ReturnType<typeof setTimeout> | undefined;
  watch(TOKENS, { recursive: true }, () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      try {
        run(false);
      } catch (e) {
        console.error(`tokens: ${e instanceof Error ? e.message : e}`);
      }
    }, 100);
  });
}
