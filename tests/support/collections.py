"""Find every Firestore collection the code names, by reading the code rather than a list.

A hand-kept list of collections would answer the wrong question. What matters is whether the
running system reaches a collection the permission matrix never declared — and the way that
happens is somebody adds a constant next to twenty others and nobody thinks about IAM. So the
source is parsed instead: module-level ``COLLECTION*`` string constants, and string literals
passed straight to ``.collection(...)``.

Two things are deliberately out of scope. Names built at runtime cannot be recovered from the
source, so a collection whose id is computed would be missed — none exists today, and one
appearing is a design change worth noticing rather than a hole to paper over. And the test
directory is excluded, because a test may legitimately probe a collection that no identity is
meant to reach.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SEARCHED = ("agents", "shared", "services", "scenarios", "scripts")

IGNORED = frozenset({"collection_nobody_declared"})
"""Names used only to prove deny-by-default, which is the opposite of a declaration."""


def collections_in_code() -> set[str]:
    """Return every collection id the source names."""
    found: set[str] = set()
    for root in SEARCHED:
        for path in (REPO / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            found |= _from_source(path.read_text())
    return found - IGNORED


def _from_source(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            found |= _from_assignment(node)
        elif isinstance(node, ast.Call):
            found |= _from_call(node)
    return found


def _from_assignment(node: ast.Assign) -> set[str]:
    if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
        return set()
    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
    if any(n.startswith("COLLECTION") or n.endswith("COLLECTION") for n in names):
        return {node.value.value}
    return set()


def _from_call(node: ast.Call) -> set[str]:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "collection":
        return set()
    if not node.args:
        return set()
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return {first.value}
    return set()
