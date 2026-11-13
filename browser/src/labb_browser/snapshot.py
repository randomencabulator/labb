"""A page as a model reads it: its text in order, with every control it can use on a line of its
own under a number, like

    # Create your account
    [1] textbox "Email" required
    [2] textbox "Password" (sensitive)
    [3] checkbox "I agree" not checked
    [4] button "Create account" (submits the form)

The numbers stay the same while an element lives, also across snapshots, so several calls in one
reply can use them. They're kept in a Map only this code holds a handle to, not on the page: the
page isn't changed, and can't read or move them."""

import re

MAKE_STATE = "() => ({ids: new Map(), els: new Map()})"

# The code that reads a frame: returns [[text, is it a control], …] and the next free number
READ = r"""
(state, {next, maxItems}) => {
  const items = [];
  let buffer = [];
  const CONTROL_ROLES = new Set(["button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "menuitemcheckbox",
    "menuitemradio", "option", "combobox", "textbox", "searchbox", "slider", "spinbutton", "treeitem", "gridcell", "listbox"]);
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "META", "LINK", "IFRAME", "FRAME", "OBJECT", "EMBED"]);
  const BLOCK = /^(ADDRESS|ARTICLE|ASIDE|BLOCKQUOTE|BR|DD|DETAILS|DIALOG|DIV|DL|DT|FIELDSET|FIGCAPTION|FIGURE|FOOTER|FORM|H[1-6]|HEADER|HR|LI|MAIN|NAV|OL|P|PRE|SECTION|TABLE|TR|UL)$/;
  const clean = text => (text || "").replace(/\s+/g, " ").trim();
  const quote = text => JSON.stringify(text.length > 120 ? text.slice(0, 120) + "…" : text);

  // Whether we're in the page's main part, to read that first on a long page
  let inMain = 0;
  const add = (text, isControl) => items.push([text, isControl, inMain > 0]);
  const flush = prefix => {
    const text = clean(buffer.join(" "));
    buffer = [];
    if (text) add((prefix || "") + text, false);
  };

  const sensitive = el => {
    if (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") return false;
    if (el.type === "password") return true;
    const auto = (el.getAttribute("autocomplete") || "").toLowerCase();
    if (/current-password|new-password|one-time-code|cc-(number|csc|exp)/.test(auto)) return true;
    return /pass(word|wd|code)|\botp\b|card.?(num|no)|cvv|cvc|\bssn\b|security.?code/i.test([el.name, el.id, el.getAttribute("aria-label"), el.placeholder].join(" "));
  };

  const textOf = el => {
    let text = clean(el.innerText !== undefined ? el.innerText : el.textContent);
    if (text) return text;
    for (const inner of el.querySelectorAll("img[alt], [aria-label], svg title")) {
      text = clean(inner.getAttribute("alt") || inner.getAttribute("aria-label") || inner.textContent);
      if (text) return text;
    }
    return "";
  };

  // A label's words: a dropdown inside it would add all its options
  const labelText = label => {
    const copy = label.cloneNode(true);
    for (const inner of copy.querySelectorAll("select, textarea, input")) inner.remove();
    return copy.textContent;
  };

  // A button that's only an icon: what its tooltip would say, or else the icon's name
  const hintOf = el => {
    for (const each of [el, ...el.querySelectorAll("[title], [data-tips], [data-tooltip], [data-title], [data-original-title]")]) {
      for (const name of ["title", "data-tips", "data-tooltip", "data-title", "data-original-title"]) {
        const text = clean(each.getAttribute(name));
        if (text) return text;
      }
    }
    const icon = el.querySelector("i[class], svg[class], span[class*=icon]") || el;
    const word = (icon.getAttribute("class") || "").split(/\s+/).find(c => /icon|^fa-|^bi-|^mdi-/i.test(c) && /[-_]/.test(c));
    return word ? `icon ${word.replace(/^.*?icon(font)?[-_]*/i, "").replace(/^(fa|bi|mdi)-/, "").replace(/[-_]+/g, " ").trim()}`.trim() : "";
  };

  const nameOf = el => {
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const root = el.getRootNode();
      const text = clean(by.split(/\s+/).map(id => { const o = root.getElementById ? root.getElementById(id) : null; return o ? o.textContent : ""; }).join(" "));
      if (text) return text;
    }
    const label = el.getAttribute("aria-label");
    if (clean(label)) return clean(label);
    if (el.labels && el.labels.length) {
      const text = clean([...el.labels].map(labelText).join(" "));
      if (text) return text;
    }
    if (el.tagName === "INPUT" && ["button", "submit", "reset"].includes(el.type) && el.value) return clean(el.value);
    if (el.tagName === "INPUT" && el.type === "image" && el.alt) return clean(el.alt);
    if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) {
      return clean(el.getAttribute("placeholder") || el.getAttribute("title") || el.name || "");
    }
    return textOf(el) || hintOf(el) || (el.tagName === "CANVAS" ? "a drawing area: browser_look with screenshot=true shows it" : "");
  };

  const roleOf = el => {
    const given = (el.getAttribute("role") || "").split(/\s+/)[0].toLowerCase();
    if (CONTROL_ROLES.has(given)) return given;
    if (given === "presentation" || given === "none") return null;
    const tag = el.tagName;
    if (tag === "A" && el.hasAttribute("href")) return "link";
    if (tag === "BUTTON" || tag === "SUMMARY") return "button";
    if (tag === "SELECT") return el.multiple ? "listbox" : "combobox";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "INPUT") {
      const type = el.type;
      if (type === "hidden") return null;
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      if (type === "checkbox" || type === "radio") return type;
      if (type === "range") return "slider";
      if (type === "number") return "spinbutton";
      if (type === "search") return "searchbox";
      if (type === "file") return "file input";
      return "textbox";
    }
    if (el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable)) return "textbox";
    if (el.hasAttribute("onclick")) return "clickable";
    const tabindex = el.getAttribute("tabindex");
    if (tabindex !== null && Number(tabindex) >= 0 && tag !== "BODY" && tag !== "HTML" && !given && clean(el.innerText).length < 100) return "clickable";
    if (!given && !["BODY", "HTML", "LABEL", "SPAN", "P", "LI", "TD", "TR", "H1", "H2", "H3", "H4", "H5", "H6"].includes(tag)) {
      if (getComputedStyle(el).cursor === "pointer" && el.parentElement && getComputedStyle(el.parentElement).cursor !== "pointer" && clean(el.innerText).length < 100 && clean(el.innerText)) return "clickable";
    }
    return null;
  };

  const number = el => {
    let id = state.ids.get(el);
    if (!id) {
      id = next++;
      state.ids.set(el, id);
      state.els.set(id, el);
    }
    return id;
  };

  const describe = (el, role) => {
    const notes = [];
    const hide = sensitive(el);
    if (hide) notes.push("sensitive: the user fills this in, with browser_handover");
    const aria = name => el.getAttribute(name);
    if (role === "checkbox" || role === "radio" || role === "switch" || role === "menuitemcheckbox" || role === "menuitemradio") {
      const on = "checked" in el ? el.checked : aria("aria-checked") === "true";
      notes.push(on ? "checked" : "not checked");
    }
    if (aria("aria-selected") === "true") notes.push("selected");
    if (aria("aria-expanded")) notes.push(aria("aria-expanded") === "true" ? "open" : "closed");
    if (aria("aria-pressed") === "true") notes.push("pressed");
    if (el.disabled || aria("aria-disabled") === "true") notes.push("disabled");
    if (el.readOnly) notes.push("read-only");
    if (el.required || aria("aria-required") === "true") notes.push("required");
    if (el.getRootNode().activeElement === el) notes.push("focused");
    if (el.willValidate && !el.validity.valid && (el.value || el.form?.classList.contains("was-validated") || el.matches(":user-invalid"))) notes.push(`invalid: ${el.validationMessage}`);
    else if (aria("aria-invalid") === "true") notes.push("invalid");
    if (role === "button" && el.form && (el.type === "submit" || el.type === "image")) notes.push("submits the form");

    let out = `${role} ${quote(nameOf(el))}`;
    if (el.tagName === "SELECT") {
      const options = [...el.options];
      const chosen = options.filter(o => o.selected).map(o => clean(o.text));
      out += ` value=${quote(chosen.join(", "))} options: ${options.slice(0, 12).map(o => clean(o.text)).join(" | ")}${options.length > 12 ? ` | … (${options.length} in all)` : ""}`;
    } else if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      if (!["checkbox", "radio", "button", "submit", "reset", "image", "file"].includes(el.type)) {
        out += hide ? "" : ` value=${quote(el.value || "")}`;
        if (el.type && !["text", "search", "textarea", "number", "password"].includes(el.type)) out += ` type=${el.type}`;
      } else if (el.type === "file" && el.files && el.files.length) {
        out += ` files: ${[...el.files].map(f => f.name).join(", ")}`;
      }
    } else if (role === "textbox" || role === "combobox" || role === "searchbox" || role === "spinbutton") {
      const value = aria("aria-valuetext") || aria("aria-valuenow") || (el.isContentEditable ? clean(el.innerText) : "");
      if (value && !nameOf(el).includes(value)) out += ` value=${quote(value)}`;
    } else if (role === "slider") {
      out += ` value=${quote(el.value || aria("aria-valuenow") || "")}`;
    } else if (role === "link") {
      const href = el.getAttribute("href") || "";
      const shown = href.startsWith("/") || href.startsWith("#") ? href : (el.href || href);
      if (shown && !shown.startsWith("javascript:") && !shown.startsWith("#")) out += ` -> ${shown.length > 70 ? shown.slice(0, 70) + "…" : shown}`;
    }
    if (notes.length) out += ` (${notes.join(", ")})`;
    return out;
  };

  const control = (el, role) => {
    flush();
    if (state.els.size > 0 && items.length >= maxItems) return;
    add(`[${number(el)}] ${describe(el, role)}`, true);
  };

  const hidden = el => {
    const style = getComputedStyle(el);
    if (style.display === "contents") return false;
    if (style.visibility === "hidden" || style.visibility === "collapse") return !el.children.length;
    if (el.getAttribute("aria-hidden") === "true" && !el.matches(":focus-within")) return true;
    return !el.checkVisibility({checkVisibilityCSS: true});
  };

  const walk = node => {
    if (items.length >= maxItems) return;
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentElement;
      if (node.nodeValue.trim() && (!parent || getComputedStyle(parent).visibility === "visible")) buffer.push(node.nodeValue);
      return;
    }
    if (node.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
      for (const child of node.childNodes) walk(child);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node;
    if (SKIP.has(el.tagName)) return;

    // A custom checkbox: the real one is hidden, and its label is what's clicked
    if (el.tagName === "LABEL" && el.control && ["checkbox", "radio"].includes(el.control.type) && !el.control.checkVisibility({checkVisibilityCSS: true}) && !hidden(el)) {
      flush();
      const input = el.control;
      const notes = [input.checked ? "checked" : "not checked"];
      if (input.disabled) notes.push("disabled");
      if (input.required) notes.push("required");
      add(`[${number(el)}] ${input.type} ${quote(clean(el.innerText) || nameOf(input))} (${notes.join(", ")})`, true);
      return;
    }
    if (hidden(el)) {
      // What's hidden stays hidden, even with a child made visible again: unusual, and rarely matters
      return;
    }

    const role = roleOf(el);
    if (role) {
      const size = el.getBoundingClientRect();
      if (size.width > 0 && size.height > 0 || el.tagName === "INPUT") {
        control(el, role);
        // A control's text is its name. Only what holds other controls is read further (a menu, a row)
        if (el.tagName !== "SELECT" && el.querySelector("a[href], button, input, select, textarea, [role=button], [role=link], [role=checkbox], [role=menuitem], [role=option], [role=tab]")) {
          for (const child of el.shadowRoot ? el.shadowRoot.childNodes : el.childNodes) walk(child);
          flush();
        }
        return;
      }
    }

    const tag = el.tagName;
    // A label's words are its control's name: not once more around it
    if (tag === "LABEL" && el.control) {
      if (el.contains(el.control)) {
        flush();
        walk(el.control);
      }
      return;
    }
    const block = BLOCK.test(tag) || ["block", "flex", "grid", "list-item", "table", "table-row"].includes(getComputedStyle(el).display);
    if (block) flush();
    const given = el.getAttribute("role");
    const isDialog = tag === "DIALOG" || given === "dialog" || given === "alertdialog";
    if (isDialog) add(`--- dialog ${quote(nameOf(el).slice(0, 60))} ---`, false);
    if (tag === "HR") add("---", false);
    const isMain = tag === "MAIN" || given === "main";
    if (isMain) inMain++;
    if (tag === "IMG") {
      // One without words is still there to look at, unless it's small enough to be an icon
      const box = el.getBoundingClientRect();
      if (clean(el.alt)) buffer.push(`[image: ${clean(el.alt)}]`);
      else if (box.width >= 64 && box.height >= 64) buffer.push("[image, no description]");
    }
    if (tag === "TD" || tag === "TH") buffer.push("|");

    if (el.shadowRoot) {
      walk(el.shadowRoot);
    } else if (tag === "SLOT" && el.assignedNodes().length) {
      for (const child of el.assignedNodes()) walk(child);
    } else {
      for (const child of el.childNodes) walk(child);
    }

    const heading = /^H([1-6])$/.exec(tag);
    if (heading) flush("#".repeat(Number(heading[1])) + " ");
    else if (tag === "LI") flush("- ");
    else if (block) flush();
    if (isMain) inMain--;
    if (isDialog) add("--- end of dialog ---", false);
  };

  if (document.body) walk(document.body);
  flush();
  return {items, next, full: items.length >= maxItems};
}
"""

# What's about to be used: enough to ask the user about it, and to say what a form would send
INSPECT = r"""
el => {
  const clean = text => (text || "").replace(/\s+/g, " ").trim();
  const labelText = label => {
    const copy = label.cloneNode(true);
    for (const inner of copy.querySelectorAll("select, textarea, input")) inner.remove();
    return copy.textContent;
  };
  const nameOf = e => clean(e.getAttribute("aria-label") || (e.labels && e.labels[0] && labelText(e.labels[0])) || e.placeholder || e.name || e.id || "");
  const isField = e => ["SELECT", "TEXTAREA"].includes(e.tagName) || (e.tagName === "INPUT" && !["button", "submit", "reset", "image"].includes(e.type));
  const isSecret = e => {
    if (e.tagName !== "INPUT" && e.tagName !== "TEXTAREA") return false;
    if (e.type === "password") return true;
    const auto = (e.getAttribute("autocomplete") || "").toLowerCase();
    if (/current-password|new-password|one-time-code|cc-(number|csc|exp)/.test(auto)) return true;
    return /pass(word|wd|code)|\botp\b|card.?(num|no)|cvv|cvc|\bssn\b|security.?code/i.test([e.name, e.id, e.getAttribute("aria-label"), e.placeholder].join(" "));
  };
  const target = el.tagName === "LABEL" && el.control ? el.control : el;
  const form = target.form || (target.closest && target.closest("form")) || null;
  const type = (target.getAttribute("type") || "").toLowerCase();
  const isButton = target.tagName === "BUTTON" || (target.tagName === "INPUT" && ["submit", "image"].includes(type));
  const submits = Boolean(form) && isButton && (target.tagName === "INPUT" || type === "submit" || type === "");
  const fields = !form ? [] : [...form.elements].filter(e => e.name || nameOf(e)).filter(e => !["submit", "button", "reset", "image", "hidden", "fieldset"].includes(e.type)).filter(e => !["checkbox", "radio"].includes(e.type) || e.checked).slice(0, 30).map(e => ({
    name: nameOf(e) || e.name,
    value: isSecret(e) ? "(hidden)" : e.tagName === "SELECT" ? [...e.selectedOptions].map(o => clean(o.text)).join(", ") : e.type === "file" ? [...e.files].map(f => f.name).join(", ") : e.type === "checkbox" || e.type === "radio" ? (e.value === "on" ? "yes" : e.value) : String(e.value).slice(0, 300),
  }));
  return {
    tag: target.tagName.toLowerCase(),
    type,
    role: target.getAttribute("role") || "",
    name: (isField(target) ? nameOf(target) : clean(el.getAttribute("aria-label") || el.innerText || el.value || el.title || nameOf(target))).slice(0, 120),
    sensitive: isSecret(target),
    multiple: Boolean(target.multiple),
    editable: target.isContentEditable || ["INPUT", "TEXTAREA"].includes(target.tagName),
    checked: "checked" in target ? target.checked : target.getAttribute("aria-checked") === "true",
    label: el.tagName === "LABEL",
    submits,
    in_form: Boolean(form),
    form: form ? {method: (target.getAttribute("formmethod") || form.getAttribute("method") || "get").toLowerCase(), action: new URL(target.getAttribute("formaction") || form.getAttribute("action") || "", document.baseURI).href, fields} : null,
    options: target.tagName === "SELECT" ? [...target.options].map(o => clean(o.text)).slice(0, 60) : [],
  };
}
"""

# Waits until the page's text stops changing, after something was done on it
SETTLE = """async ({maxMs, quietMs}) => {
    const start = performance.now();
    let last = -1, since = start;
    while (performance.now() - start < maxMs) {
        const text = document.body ? document.body.innerText : "";
        const now = performance.now();
        if (text.length !== last) {
            last = text.length;
            since = now;
        } else if (now - since >= quietMs) {
            return;
        }
        await new Promise(r => setTimeout(r, 100));
    }
}"""


def render(title: str, url: str, parts: list[list], start: int, max_chars: int, find: str = "") -> str:
    """The snapshot as text: what fits from `start`, and the controls further down, so the
    Submit button at the bottom of a long page is never out of reach"""

    head = f"Page: {title or '(no title)'}\nAddress: {url}\n"
    # On a long page, the page's main part first; what's around it (menus and the like) after
    if sum(len(line[0]) + 1 for line in parts) > max_chars and any(line[2] for line in parts if len(line) > 2):
        main = [line for line in parts if line[2]]
        around = [line for line in parts if not line[2]]
        parts = [*main, ["--- around that on the page ---", False, False], *around]
    lines = [line for line in parts if not find or find.lower() in line[0].lower()]
    if find and not lines:
        return f"{head}\nNothing on the page mentions {find!r}."
    body = "\n".join(line[0] for line in lines)
    if not body.strip():
        return f"{head}\n(The page shows no text and no controls. It may draw on a canvas: browser_look with screenshot=true shows it.)"
    start = max(0, min(start, len(body)))
    part = body[start:start + max_chars]
    if start + max_chars >= len(body):
        return f"{head}\n{part}"
    # Cut at the end of a line
    if (cut := part.rfind("\n")) > max_chars // 2:
        part = part[:cut]
    end = start + len(part)
    rest = body[end:]
    controls = re.findall(r"^\[\d+\] .*$", rest, re.M)
    more = "\n".join(controls[:30])
    note = f"\n\n[… {len(rest):,} more characters: read on with browser_look(start={end})]"
    if controls:
        note += f"\nControls further down:\n{more}" + (f"\n… and {len(controls) - 30} more" if len(controls) > 30 else "")
    return f"{head}\n{part}{note}"
