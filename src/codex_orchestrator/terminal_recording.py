"""Opt-in local recording of the orchestrator's Python terminal output."""

import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

from codex_orchestrator.context_files import sensitive_text


class TerminalLog:
    def __init__(self, log):
        self.log = log
        self.lock = Lock()
        self.pending = ""
        self.offset = 0

    @staticmethod
    def safe_line(text):
        return "[Recognizable credential output omitted]" if sensitive_text(text) else text

    def append(self, text):
        with self.lock:
            # Rewrite the unfinished line so prompts stay visible and credentials
            # split across multiple writes can be redacted as a single line.
            lines = (self.pending + text).split("\n")
            self.pending = lines.pop()
            self.log.seek(self.offset)
            for line in lines:
                self.log.write(self.safe_line(line) + "\n")
            self.offset = self.log.tell()
            self.log.write(self.safe_line(self.pending))
            self.log.truncate()
            self.log.flush()


class TerminalOutput:
    def __init__(self, stream, recorder):
        self.stream = stream
        self.recorder = recorder

    def write(self, text):
        written = self.stream.write(text)
        self.recorder.append(text)
        return written

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextmanager
def record_terminal(directory: Path):
    folder = directory.resolve() / "terminal-logs"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.is_symlink():
        raise ValueError("Choose a regular directory for terminal logs.")
    prefix = datetime.now().strftime("cycle-%Y%m%d-%H%M%S-")
    with NamedTemporaryFile(
        mode="w+", encoding="utf-8", prefix=prefix, suffix=".log", dir=folder, delete=False
    ) as log:
        recorder = TerminalLog(log)
        print(f"Terminal recording: {log.name}", flush=True)
        with (
            redirect_stdout(TerminalOutput(sys.stdout, recorder)),
            redirect_stderr(TerminalOutput(sys.stderr, recorder)),
        ):
            yield Path(log.name)
