"""AST inspection of generated code, before it reaches a container.

This is defence in depth, not the security boundary. The container is the
boundary: `network_mode=none` removes the network at the kernel level, so code
that imports `socket` still cannot reach anything. What this adds is a fast,
legible refusal — an agent that tries to fetch a URL gets told why immediately,
rather than after a container start and a confusing connection error.

It is deliberately not a sandbox in itself. Anyone who has tried to make one out
of AST filtering has eventually found a way around it; treating this as the
control rather than the hint is the mistake.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

#: Modules a calculation has no business importing. Networking is the obvious
#: one; the rest are ways to escape the interpreter or start another process.
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "socket", "ssl", "http", "urllib", "urllib2", "urllib3", "requests",
        "httpx", "aiohttp", "ftplib", "smtplib", "poplib", "imaplib",
        "telnetlib", "xmlrpc", "asyncio", "subprocess", "multiprocessing",
        "ctypes", "cffi", "pty", "signal", "socketserver", "webbrowser",
        "importlib", "pkgutil", "runpy", "gc", "atexit",
    }
)

#: Builtins that turn a static check into a formality if left available.
FORBIDDEN_BUILTINS: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "__import__", "globals", "locals", "vars", "breakpoint"}
)

#: `os` is allowed — path handling is genuinely needed — but not the parts of it
#: that start processes or alter the environment.
FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "system", "popen", "spawn", "spawnl", "spawnv", "execv", "execve",
        "execl", "fork", "forkpty", "kill", "setuid", "setgid",
    }
)


@dataclass
class StaticCheckResult:
    ok: bool
    violations: list[str] = field(default_factory=list)
    #: Imports that were seen and allowed, for the audit record.
    imports: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return "; ".join(self.violations)


class _Inspector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.imports: list[str] = []

    def _root(self, name: str) -> str:
        return name.split(".", 1)[0]

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = self._root(alias.name)
            self.imports.append(alias.name)
            if root in FORBIDDEN_MODULES:
                self.violations.append(
                    f"line {node.lineno}: importing '{alias.name}' is not permitted"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root = self._root(module)
        self.imports.append(module)
        if root in FORBIDDEN_MODULES:
            self.violations.append(
                f"line {node.lineno}: importing from '{module}' is not permitted"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
            self.violations.append(
                f"line {node.lineno}: calling {node.func.id}() is not permitted"
            )
        if isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_ATTRIBUTES:
            self.violations.append(
                f"line {node.lineno}: calling .{node.func.attr}() is not permitted"
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Attribute chains through dunders are the classic route out of a
        # restricted namespace: ().__class__.__bases__[0].__subclasses__().
        if node.attr.startswith("__") and node.attr.endswith("__"):
            if node.attr not in {"__name__", "__doc__", "__file__", "__dict__", "__len__"}:
                self.violations.append(
                    f"line {node.lineno}: accessing '{node.attr}' is not permitted"
                )
        self.generic_visit(node)


def check(code: str) -> StaticCheckResult:
    """Inspect generated code. Never raises on bad syntax — reports it."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return StaticCheckResult(
            ok=False, violations=[f"line {exc.lineno}: syntax error — {exc.msg}"]
        )

    inspector = _Inspector()
    inspector.visit(tree)
    return StaticCheckResult(
        ok=not inspector.violations,
        violations=inspector.violations,
        imports=sorted(set(inspector.imports)),
    )
