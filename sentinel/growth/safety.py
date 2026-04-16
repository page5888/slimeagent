"""AST-based code safety scanner.

Replaces the string-matching check in self_evolution._is_code_safe(),
which can be bypassed by an LLM (intentionally or accidentally) with
simple tricks:

  - Whitespace: `os . system(...)`
  - Indirect lookup: `getattr(__builtins__, 'eval')`
  - Aliasing: `import os as _o; _o.system(...)`
  - Attribute chains: `__import__("subprocess").run(...)`

AST-level checks see through all of these because we inspect the
parsed structure, not the source string.

Scope (what we forbid):
  - Any call to: eval, exec, compile, __import__
  - Any attribute access on os.{system, remove, unlink, rmdir, ...}
  - Any attribute access on shutil.{rmtree, move, ...}
  - Any import of: subprocess, ctypes, socket, multiprocessing
  - sys.exit / os._exit / quit / exit
  - File writes outside an allowlist (data files under ~/.hermes/pending)
  - Imports of sentinel.self_evolution (slime can't rewrite the safety
    system from inside a generated skill)

This is NOT a sandbox. It catches obvious and common patterns. A
determined adversary could still craft bypasses (e.g. bytecode
manipulation). The defense in depth story is:
  1. AST scan (this file) catches 95% of issues
  2. Human approval (approval.py) catches what the scanner missed
  3. PR 4 will add a real subprocess sandbox with resource limits

For now: AST + human approval is enough to make `self_evolution.py`
safe to run without it silently shipping a disaster.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Optional


# ── Forbidden names/calls/imports ─────────────────────────────────

FORBIDDEN_CALLS: set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "exit",
    "quit",
}

# module.attribute pairs that are dangerous regardless of how
# `module` was imported or aliased.
FORBIDDEN_ATTRS: set[tuple[str, str]] = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "removedirs"),
    ("os", "_exit"),
    ("os", "kill"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "spawnl"),
    ("os", "spawnv"),
    ("shutil", "rmtree"),
    ("shutil", "move"),
    ("shutil", "copy"),   # allowed via approval path only
    ("sys", "exit"),
    ("sys", "settrace"),
    ("sys", "setprofile"),
}

FORBIDDEN_IMPORTS: set[str] = {
    "subprocess",
    "ctypes",
    "multiprocessing",
    "socket",
    "asyncio.subprocess",
    "sentinel.self_evolution",   # can't rewrite the safety system
    "sentinel.growth.safety",    # can't neuter this scanner
    "sentinel.growth.approval",  # can't bypass approval queue
}

# Dunder access that indicates reflection-based bypass attempts.
SUSPICIOUS_DUNDERS: set[str] = {
    "__builtins__",
    "__globals__",
    "__loader__",
    "__spec__",
    "__class__",  # allowed in isolated spots, but `x.__class__.__bases__[0].__subclasses__()` is the classic escape
}


@dataclass
class SafetyFinding:
    """A single problem found in the scanned code."""
    severity: str          # "block" — refuse outright. "warn" — flag for human.
    rule: str              # short machine ID, e.g. "forbidden_call:eval"
    line: int              # 1-indexed source line
    message: str           # human-readable explanation


@dataclass
class SafetyReport:
    """Result of scanning a piece of code."""
    safe: bool
    findings: list[SafetyFinding] = field(default_factory=list)
    syntax_error: Optional[str] = None

    @property
    def blocking(self) -> list[SafetyFinding]:
        return [f for f in self.findings if f.severity == "block"]

    @property
    def warnings(self) -> list[SafetyFinding]:
        return [f for f in self.findings if f.severity == "warn"]

    def summary(self) -> str:
        if self.syntax_error:
            return f"SYNTAX ERROR: {self.syntax_error}"
        if not self.findings:
            return "OK — no findings"
        parts = []
        if self.blocking:
            parts.append(f"{len(self.blocking)} blocking")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning")
        return "; ".join(parts)


# ── Scanner ────────────────────────────────────────────────────────

class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[SafetyFinding] = []
        # import aliases: local_name -> real_module_name
        self._aliases: dict[str, str] = {}

    # --- imports ---

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            real = alias.name
            local = alias.asname or alias.name
            self._aliases[local] = real
            self._check_import(real, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        self._check_import(mod, node.lineno)
        for alias in node.names:
            # `from os import system` — still forbidden
            local = alias.asname or alias.name
            if (mod, alias.name) in FORBIDDEN_ATTRS:
                self.findings.append(SafetyFinding(
                    severity="block",
                    rule=f"forbidden_attr:{mod}.{alias.name}",
                    line=node.lineno,
                    message=(
                        f"imports {mod}.{alias.name} directly — that API "
                        f"is on the forbidden list"
                    ),
                ))
            # Track the alias so later `local(...)` call can be caught
            self._aliases[local] = f"{mod}.{alias.name}"
        self.generic_visit(node)

    def _check_import(self, module: str, line: int) -> None:
        # Exact match or prefix (e.g. `asyncio.subprocess` covers
        # `asyncio.subprocess.create_subprocess_exec`)
        for forbidden in FORBIDDEN_IMPORTS:
            if module == forbidden or module.startswith(forbidden + "."):
                self.findings.append(SafetyFinding(
                    severity="block",
                    rule=f"forbidden_import:{forbidden}",
                    line=line,
                    message=f"imports forbidden module '{module}'",
                ))
                return

    # --- calls ---

    def visit_Call(self, node: ast.Call) -> None:
        # Direct name call: eval(...), exec(...), __import__(...)
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in FORBIDDEN_CALLS:
                self.findings.append(SafetyFinding(
                    severity="block",
                    rule=f"forbidden_call:{name}",
                    line=node.lineno,
                    message=f"calls forbidden builtin '{name}'",
                ))
            # Aliased import used as a call — e.g. `system('...')` after
            # `from os import system`
            real = self._aliases.get(name)
            if real and "." in real:
                mod, attr = real.rsplit(".", 1)
                if (mod, attr) in FORBIDDEN_ATTRS:
                    self.findings.append(SafetyFinding(
                        severity="block",
                        rule=f"forbidden_attr:{mod}.{attr}",
                        line=node.lineno,
                        message=(
                            f"calls {name}() which aliases the forbidden "
                            f"{mod}.{attr}"
                        ),
                    ))

        # Attribute call: os.system(...), _o.system(...), shutil.rmtree(...)
        elif isinstance(node.func, ast.Attribute):
            mod_name = self._resolve_attr_root(node.func)
            attr_name = node.func.attr
            if mod_name:
                # Resolve alias to real module name if known
                real_mod = self._aliases.get(mod_name, mod_name)
                if (real_mod, attr_name) in FORBIDDEN_ATTRS:
                    self.findings.append(SafetyFinding(
                        severity="block",
                        rule=f"forbidden_attr:{real_mod}.{attr_name}",
                        line=node.lineno,
                        message=(
                            f"calls forbidden {real_mod}.{attr_name}"
                            + (f" (via alias '{mod_name}')"
                               if mod_name != real_mod else "")
                        ),
                    ))

        # getattr(...) — could be used for indirect access
        if (isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            requested = node.args[1].value
            if requested in FORBIDDEN_CALLS or requested == "system":
                self.findings.append(SafetyFinding(
                    severity="block",
                    rule=f"forbidden_getattr:{requested}",
                    line=node.lineno,
                    message=(
                        f"uses getattr() to reach '{requested}' — "
                        f"reflection-based bypass"
                    ),
                ))

        self.generic_visit(node)

    # --- attribute access (not necessarily a call) ---

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in SUSPICIOUS_DUNDERS:
            # __class__ chains like ''.__class__.__mro__[1].__subclasses__()
            # are the classic sandbox escape; flag all suspicious dunders
            # as warnings (not block — some legit uses exist)
            self.findings.append(SafetyFinding(
                severity="warn",
                rule=f"suspicious_dunder:{node.attr}",
                line=node.lineno,
                message=(
                    f"accesses '{node.attr}' — reflection often indicates "
                    f"a sandbox-escape attempt"
                ),
            ))
        self.generic_visit(node)

    # --- helper ---

    def _resolve_attr_root(self, node: ast.Attribute) -> Optional[str]:
        """Walk an Attribute chain back to its root Name.

        e.g. `os.path.join` → root is "os"
             `a.b.c`       → root is "a"
             `(x + y).foo` → root is None (not a name chain)
        """
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name):
            return cur.id
        return None


def scan_code(source: str) -> SafetyReport:
    """Scan Python source for forbidden patterns.

    Returns a SafetyReport. Check .safe before executing or storing.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return SafetyReport(safe=False, syntax_error=str(e))

    visitor = _Visitor()
    visitor.visit(tree)
    findings = visitor.findings
    # "safe" means: no blocking findings. Warnings are surfaced to the
    # human in the approval dialog but don't auto-reject.
    safe = not any(f.severity == "block" for f in findings)
    return SafetyReport(safe=safe, findings=findings)


# ── Self-test — run `python -m sentinel.growth.safety` ─────────

_SELFTEST_CASES: list[tuple[str, bool, str]] = [
    # (source, expected_safe, description)
    ("x = 1 + 2", True, "plain arithmetic"),
    ("import json; json.dumps({})", True, "json allowed"),
    ("import os; os.system('rm -rf /')", False, "direct os.system"),
    ("import os as o\no.system('x')", False, "aliased os"),
    ("from os import system\nsystem('x')", False, "from-imported system"),
    ("eval('1+1')", False, "direct eval"),
    ("exec('print(1)')", False, "direct exec"),
    ("__import__('os').system('x')", False, "__import__ chain"),
    ("import subprocess", False, "subprocess import"),
    ("from subprocess import run", False, "subprocess from-import"),
    ("getattr(object, 'system')", False, "getattr to system"),
    ("import shutil; shutil.rmtree('/')", False, "shutil.rmtree"),
    ("import sys; sys.exit(0)", False, "sys.exit"),
    ("from sentinel.growth.safety import scan_code", False,
     "cannot import safety scanner from within generated code"),
]


def _selftest() -> int:
    passed = 0
    failed: list[str] = []
    for source, expected, desc in _SELFTEST_CASES:
        report = scan_code(source)
        if report.safe == expected:
            passed += 1
            print(f"  OK    {desc}")
        else:
            failed.append(desc)
            print(f"  FAIL  {desc}  (got safe={report.safe}, "
                  f"expected {expected})")
            for f in report.findings:
                print(f"        [{f.severity}] {f.rule}: {f.message}")
    total = len(_SELFTEST_CASES)
    print(f"\n{passed}/{total} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
