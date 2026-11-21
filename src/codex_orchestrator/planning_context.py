"""Bounded local project inventory and shared rules for efficient planning."""

import json
import os
import re
from pathlib import Path

from codex_orchestrator.codex import CodexError
from codex_orchestrator.context_files import is_private_path, sensitive_text

MAX_MAP_BYTES = 6 * 1024
MAX_MAP_FILES = 80
MAX_ENTRIES = 1200
MAX_DEPTH = 6
MAX_PROMPT_BYTES = 64 * 1024
EXCLUDED = {
    "plans",
    "logs",
    "build",
    "dist",
    "vendor",
    "venv",
    "env",
    "__pycache__",
    ".idea",
    ".codex",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "coverage",
}
SAFE_HIDDEN = {".github", ".agents", ".gitignore"}
SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".sql",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".php",
    ".cs",
    ".swift",
    ".kt",
    ".sh",
}
IMPORTANT = {"readme", "agents", "architecture", "pyproject", "package", "makefile"}

NO_EXPLORATION = (
    "Use supplied requirements only. Do not call tools, list directories, read project "
    "files, or inspect Git. Ask a focused clarification if a missing fact materially "
    "changes the requirements. Defer repository feasibility and technical details to "
    "the architect. "
)
FOCUSED_EXPLORATION = (
    "Use the compact repository map as the starting point; it is path metadata, not "
    "verified file contents or a complete inventory. Do not repeat a full directory listing. "
    "Read only files necessary to establish feasibility, contracts or ownership. Prefer "
    "one focused batch of reads and narrow excerpts: target at most 6 distinct files, "
    "4 inspection calls, and 16 KiB per file. These are planning targets, not test requirements. "
    "If more inspection is essential, return a focused clarification or propose a scoped "
    "investigation instead of guessing. No recursive file dumps, repeated reads, diagnostics "
    "unrelated to the brief, or full test runs at planning time. "
)


def repository_map(project: Path, goal: str) -> dict:
    """List relevant path names without reading contents, following symlinks or running Git."""
    hints = set(re.findall(r"[a-z0-9]{3,}", goal.lower()))
    candidates = []
    root = project.resolve()
    pending = [(root, 0)]
    inspected = 0
    incomplete = False
    while pending and inspected < MAX_ENTRIES:
        folder, depth = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    inspected += 1
                    if inspected > MAX_ENTRIES:
                        incomplete = True
                        break
                    relative = Path(entry.path).relative_to(root)
                    name = entry.name.lower()
                    if (
                        name in EXCLUDED
                        or name.endswith((".design.json", ".cycles.json"))
                        or re.fullmatch(r"session-[0-9a-f]+\.json", name)
                        or (name.startswith(".") and name not in SAFE_HIDDEN)
                        or is_private_path(relative)
                        or sensitive_text(relative.as_posix())
                        or any(ord(c) < 32 for c in relative.as_posix())
                    ):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < MAX_DEPTH:
                            pending.append((Path(entry.path), depth + 1))
                        else:
                            incomplete = True
                    elif entry.is_file(follow_symlinks=False) and (
                        relative.suffix.lower() in SOURCE_SUFFIXES
                        or relative.stem.lower() in IMPORTANT
                        or name == ".gitignore"
                    ):
                        words = set(re.findall(r"[a-z0-9]{3,}", relative.as_posix().lower()))
                        score = len(words & hints) * 4 + (relative.stem.lower() in IMPORTANT)
                        candidates.append((-score, len(relative.parts), relative.as_posix()))
        except OSError:
            incomplete = True
    paths = []
    for _, _, name in sorted(candidates):
        if len(paths) >= MAX_MAP_FILES:
            incomplete = True
            break
        if len(json.dumps(paths + [name], ensure_ascii=False).encode()) > MAX_MAP_BYTES:
            incomplete = True
            break
        paths.append(name)
    return {"paths": paths, "partial": incomplete or bool(pending)}


def planning_prompt(instructions: str, data: dict) -> str:
    """Fail before inference if approved inputs cannot fit; never silently truncate them."""
    prompt = instructions + "\n" + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(prompt.encode()) > MAX_PROMPT_BYTES:
        raise CodexError(
            "Planning input exceeds the 64 KiB application prompt budget. "
            "Narrow the goal or revise the supplied planning context before retrying."
        )
    return prompt
