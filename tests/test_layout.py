"""The package's layering: the core (src/labb, tools/) imports neither cli/ nor server/,
and those two don't import each other, so the terminal never pulls in the server or back."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "src" / "labb"
FORBIDDEN = {"core": ("labb.cli", "labb.server"), "cli": ("labb.server",), "server": ("labb.cli",)}


def imported(path: Path) -> set[str]:
    """Every module `path` imports, with `from labb import x` as labb.x"""
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names |= {node.module} | {f"{node.module}.{alias.name}" for alias in node.names}
    return names


@pytest.mark.parametrize("path", sorted(PACKAGE.rglob("*.py")), ids=lambda path: str(path.relative_to(PACKAGE)))
def test_layers_only_import_downwards(path):
    part = path.relative_to(PACKAGE).parts[0]
    layer = part if part in ("cli", "server") else "core"
    assert [name for name in imported(path) if name.startswith(FORBIDDEN[layer])] == []
