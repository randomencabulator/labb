"""`{{ ... }}` references in a step's settings: `{{trigger.topic}}`, `{{research}}`,
`{{research.items.0.title}}`, `{{item}}` in a for-each, and `{{x | json}}` for JSON.

A setting that's nothing but one reference keeps the value's type (a list stays a list, for a
for-each or a Python step); anywhere else, values are written into the text, with lists and
objects as JSON.
"""

import json
import re

REFERENCE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_-]+)*)\s*(?:\|\s*([a-z]+)\s*)?\}\}")
FILTERS = ("json", "lines", "length", "upper", "lower", "trim")


class TemplateError(ValueError):
    """A reference that can't be filled in, said for whoever built the workflow"""


def references(text) -> list[str]:
    """The paths a setting refers to ("research.title"), in any string inside it"""

    if isinstance(text, str):
        return [match.group(1) for match in REFERENCE.finditer(text)]
    if isinstance(text, dict):
        return [path for value in text.values() for path in references(value)]
    if isinstance(text, list):
        return [path for value in text for path in references(value)]
    return []


def roots(value) -> set[str]:
    """The step keys (and `trigger`, `item`…) a setting refers to"""
    return {path.split(".", 1)[0] for path in references(value)}


def lookup(context: dict, path: str):
    parts = path.split(".")
    if parts[0] not in context:
        raise TemplateError(f"{{{{{path}}}}}: there's no step called {parts[0]}")
    value = context[parts[0]]
    for i, part in enumerate(parts[1:], start=1):
        where = ".".join(parts[:i])
        if isinstance(value, str):
            # A step that wrote JSON as text can still be looked into
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif value is None:
            return None
        else:
            raise TemplateError(f"{{{{{path}}}}}: {where} has no {part}")
    return value


def as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, indent=2)


def apply(value, name: str | None):
    if name is None:
        return value
    if name == "json":
        return json.dumps(value, ensure_ascii=False)
    if name == "lines":
        return [line.strip() for line in as_text(value).splitlines() if line.strip()]
    if name == "length":
        return len(value) if isinstance(value, (list, dict, str)) else 0
    if name == "upper":
        return as_text(value).upper()
    if name == "lower":
        return as_text(value).lower()
    if name == "trim":
        return as_text(value).strip()
    raise TemplateError(f"There's no | {name} (there's {', '.join(FILTERS)})")


def render(value, context: dict):
    """Fill in a setting: strings anywhere inside lists and objects too"""

    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, context) for item in value]
    if not isinstance(value, str):
        return value
    whole = REFERENCE.fullmatch(value.strip())
    if whole:
        return apply(lookup(context, whole.group(1)), whole.group(2))
    return REFERENCE.sub(lambda m: as_text(apply(lookup(context, m.group(1)), m.group(2))), value)


def render_text(value, context: dict) -> str:
    return as_text(render(value, context))
