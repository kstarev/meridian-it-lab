"""Offline, no AI and read-only, checked in the source itself (OAR-004, OAR-005, OAR-007).

The lab's test-wide guard already blocks the network at run time (the root conftest.py).
These tests check the code can't even try: it imports no network or AI library, and no name
in it revokes or deletes anything.
"""

import ast
import tomllib
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "meridian_oauth_radar"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# Top-level modules the package must never import: network, shelling out, AI SDKs.
FORBIDDEN_IMPORTS = {
    "aiohttp",
    "anthropic",
    "asyncio",
    "ftplib",
    "http",
    "httpx",
    "ollama",
    "openai",
    "requests",
    "smtplib",
    "socket",
    "ssl",
    "subprocess",
    "urllib",
    "urllib3",
    "websockets",
}
FORBIDDEN_WORDS = ("revoke", "delete")


def sources() -> list[Path]:
    files = sorted(SRC.rglob("*.py"))
    assert files
    return files


def imported(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
    return names


def test_the_package_imports_no_network_or_ai_library() -> None:
    found = {
        path.name: sorted(imported(ast.parse(path.read_text(encoding="utf-8"))) & FORBIDDEN_IMPORTS)
        for path in sources()
    }
    assert not {name: hits for name, hits in found.items() if hits}


def test_no_name_in_the_source_revokes_or_deletes() -> None:
    found = {
        path.name: sorted(
            name
            for name in identifiers(ast.parse(path.read_text(encoding="utf-8")))
            if any(word in name.casefold() for word in FORBIDDEN_WORDS)
        )
        for path in sources()
    }
    assert not {name: hits for name, hits in found.items() if hits}


def test_the_package_depends_on_nothing() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == []
