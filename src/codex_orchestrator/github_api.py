"""Bounded GitHub CLI transport using the account's existing authentication."""

import json
import os
import shutil
import subprocess
from tempfile import TemporaryFile


def request(*args, body=None):
    executable = shutil.which("gh")
    if not executable:
        raise ValueError("Install GitHub CLI and run the GitHub connection command first.")
    with TemporaryFile() as output, TemporaryFile() as errors:
        try:
            result = subprocess.run(
                [executable, *args],
                input=json.dumps(body).encode() if body is not None else b"",
                stdout=output,
                stderr=errors,
                env=dict(os.environ, GH_PROMPT_DISABLED="1"),
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(
                "GitHub request interrupted. Check PR status before retrying."
            ) from exc
        if result.returncode:
            raise ValueError(
                "GitHub rejected the request. Check sign-in, repository access, "
                "branch rules and PR status before retrying."
            )
        if output.tell() > 1024 * 1024:
            raise ValueError("GitHub response exceeds the local inspection limit.")
        output.seek(0)
        try:
            return json.load(output)
        except ValueError as exc:
            raise ValueError("GitHub returned an invalid response. Inspect PR status.") from exc
