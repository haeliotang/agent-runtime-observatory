"""Deterministic simulated tools operating on an in-memory workspace.

The scripted runtime is not trying to be a useful agent; it is trying to be a
*replayable* one. Every tool is a pure function of (workspace state, args), so
a recorded run can be re-executed later and compared digest-by-digest. That is
the property real agent runtimes lose first, and the one this repo exists to
demonstrate how to keep.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from aro_schema import digest_obj


class ToolError(Exception):
    """A tool failed in a way the agent could not proceed past."""


class Workspace:
    """In-memory file tree seeded from a directory on disk.

    Tools mutate the in-memory copy only, so runs are hermetic: executing a
    script never touches the example directory it was seeded from.
    """

    def __init__(self, files: dict[str, str] | None = None):
        self.files: dict[str, str] = dict(files or {})

    @classmethod
    def from_dir(cls, root: Path) -> Workspace:
        files = {}
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[path.relative_to(root).as_posix()] = path.read_text()
        return cls(files)

    def digest(self) -> str:
        return digest_obj(self.files)


def read_file(ws: Workspace, args: dict) -> str:
    path = args["path"]
    if path not in ws.files:
        raise ToolError(f"file not found: {path}")
    return ws.files[path]


def write_file(ws: Workspace, args: dict) -> str:
    content = args["content"]
    ws.files[args["path"]] = content
    return f"wrote {len(content)} chars to {args['path']}"


def apply_patch(ws: Workspace, args: dict) -> str:
    path, find, replace = args["path"], args["find"], args["replace"]
    if path not in ws.files:
        raise ToolError(f"file not found: {path}")
    if find not in ws.files[path]:
        raise ToolError(f"pattern not found in {path}")
    ws.files[path] = ws.files[path].replace(find, replace, 1)
    return f"patched {path}: 1 occurrence replaced"


def run_tests(ws: Workspace, args: dict) -> str:
    """Fake test runner: deterministic on workspace content.

    A workspace "fails tests" while any non-test file still contains the
    marker string BUG. This gives examples a red -> patch -> green arc without
    a real interpreter in the loop.
    """
    test_files = [p for p in sorted(ws.files) if Path(p).name.startswith("test_")]
    if not test_files:
        return "no tests found"
    n_tests = sum(ws.files[p].count("def test_") for p in test_files)
    buggy = [p for p in sorted(ws.files) if "BUG" in ws.files[p] and p not in test_files]
    if buggy:
        return f"{n_tests} collected: FAILED (unresolved BUG marker in {', '.join(buggy)})"
    return f"{n_tests} collected: all passed"


def web_fetch(ws: Workspace, args: dict) -> str:
    """Offline fetch: URLs resolve against fixture files under corpus/."""
    url = args["url"]
    parsed = urlparse(url)
    key = f"corpus/{parsed.netloc}{parsed.path}"
    for candidate in (key, key + ".md", key.rstrip("/") + "/index.md"):
        if candidate in ws.files:
            return ws.files[candidate]
    raise ToolError(f"offline fixture missing for {url}")


def shell(ws: Workspace, args: dict) -> str:
    """Simulated shell: `ls` is answered from the workspace, everything else
    is a no-op echo. Nothing ever reaches a real shell."""
    cmd = args["cmd"].strip()
    if cmd == "ls" or cmd.startswith("ls "):
        return "\n".join(sorted(ws.files))
    return f"simulated shell (no-op sandbox): `{cmd}` -> exit 0"


def summarize(ws: Workspace, args: dict) -> str:
    """Deterministic extractive summary: title line plus size stats."""
    text = args["text"] if "text" in args else read_file(ws, {"path": args["path"]})
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = lines[0] if lines else "(empty)"
    return f"{title} — {len(text.split())} words, {len(lines)} non-empty lines"


TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "apply_patch": apply_patch,
    "run_tests": run_tests,
    "web_fetch": web_fetch,
    "shell": shell,
    "summarize": summarize,
}
