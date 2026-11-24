"""Bounded local text resolution for explicitly selected project and skill files."""

import codecs
import hashlib
import os
import re
import stat
from pathlib import Path

from codex_orchestrator.codex import CodexError
from codex_orchestrator.context_models import ContextLimits, ContextResource
from codex_orchestrator.design_validation import project_path

_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
_PRIVATE_NAMES = {
    "auth.json",
    "secrets.toml",
    "secrets.json",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}
_PRIVATE_DIRS = {
    ".git",
    ".ssh",
    ".aws",
    ".azure",
    ".gcloud",
    ".venv",
    "node_modules",
    "terminal-logs",
}


def sensitive_text(text: str) -> bool:
    return bool(_SECRET.search(text))


def is_private_path(path: Path) -> bool:
    name = path.name.lower()
    return (
        name == ".env"
        or name.startswith(".env.")
        or name in _PRIVATE_NAMES
        or any(
            part.endswith(
                (
                    ".codex-sessions.json",
                    ".codex-sessions.lock",
                    ".codex-turns",
                    ".compact-plans",
                    ".runs",
                    ".review-inputs",
                    ".chat.json",
                    ".reviews.json",
                    ".pull-requests.json",
                    ".coordination",
                )
            )
            for part in path.parts
        )
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
        or bool(set(path.parts) & _PRIVATE_DIRS)
        or any(
            part == ".codex"
            and following in {"sessions", "archived_sessions", "logs", "history.jsonl"}
            for part, following in zip(path.parts, path.parts[1:], strict=False)
        )
    )


def resolve_file(project: Path, item: ContextResource) -> Path:
    if item.kind != "skill":
        logical = project / item.reference
        resolved = project_path(project, item.reference)
    else:
        logical = Path(item.reference).expanduser()
        if not logical.is_absolute():
            logical = project / logical
        resolved = logical.resolve()
        roots = [
            project.resolve(),
            Path.home() / ".codex" / "skills",
            Path.home() / ".agents" / "skills",
            Path.home() / ".codex" / "plugins",
        ]
        if resolved.name != "SKILL.md" or not any(
            resolved.is_relative_to(r.resolve()) for r in roots
        ):
            raise ValueError(
                "Skill must be a SKILL.md in the project or a configured local skill root."
            )
    if is_private_path(logical) or is_private_path(resolved):
        raise ValueError("Credential, private-key, or generated dependency paths are excluded.")
    return resolved


def read_file(project: Path, item: ContextResource, limits: ContextLimits) -> ContextResource:
    try:
        path = resolve_file(project, item)
        # Non-blocking and no-follow protect against a special file or final symlink replacement.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Only regular text files can be supplied.")
            item.size_bytes = info.st_size
            raw = stream.read(limits.max_file_bytes + 1)
            final = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                final.st_dev,
                final.st_ino,
                final.st_size,
                final.st_mtime_ns,
                final.st_ctime_ns,
            ) or len(raw) != min(info.st_size, limits.max_file_bytes + 1):
                raise ValueError("File changed while being read; refresh the context.")
        truncated = len(raw) > limits.max_file_bytes
        included = raw[: limits.max_file_bytes]
        text = codecs.getincrementaldecoder("utf-8")().decode(included, final=not truncated)
        if "\x00" in text:
            raise ValueError("Binary files are excluded from text context.")
        if sensitive_text(text):
            raise ValueError(
                "File contains a recognizable credential or private key; content excluded."
            )
        item.content = text
        item.sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        item.status = "truncated" if truncated else "included"
    except FileNotFoundError:
        item.status = "missing"
    except (OSError, ValueError, UnicodeError, CodexError) as exc:
        item.status = "excluded"
        item.reason += " " + str(exc)
    return item
