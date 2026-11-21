"""Discover picker-visible model IDs via the installed Codex app-server, not auth files."""

import json
import os
import re
import select
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from codex_orchestrator.codex import CodexError, find_codex
from codex_orchestrator.session import ModelPolicy

CATALOG_TIMEOUT = 15
MAX_CATALOG_BYTES = 2 * 1024 * 1024
FAMILY = re.compile(r"^gpt-(\d+(?:\.\d+)*?)-(luna|terra|sol|astra)$", re.I)


@dataclass(frozen=True)
class ModelOffer:
    id: str
    label: str
    default: bool = False


class _CatalogClient:
    def __init__(self, process):
        self.process = process
        self.buffer = bytearray()
        self.deadline = monotonic() + CATALOG_TIMEOUT
        self.received = 0

    def send(self, data):
        try:
            self.process.stdin.write(json.dumps(data).encode() + b"\n")
            self.process.stdin.flush()
        except OSError as exc:
            raise CodexError("Codex model catalog server closed unexpectedly.") from exc

    def result(self, request_id):
        while True:
            while (end := self.buffer.find(b"\n")) >= 0:
                line = self.buffer[:end]
                del self.buffer[: end + 1]
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict) or message.get("id") != request_id:
                    continue
                result = message.get("result")
                if not isinstance(result, dict) or "error" in message:
                    raise CodexError("Codex rejected the model catalog request.")
                return result
            remaining = self.deadline - monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise CodexError("Timed out reading the Codex model catalog.")
            block = os.read(self.process.stdout.fileno(), 16384)
            if not block:
                raise CodexError("Codex model catalog server exited before answering.")
            self.received += len(block)
            if self.received > MAX_CATALOG_BYTES:
                raise CodexError("Codex model catalog exceeded the response limit.")
            self.buffer.extend(block)


def parse_offers(rows):
    offers = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("hidden") is True:
            continue
        model = row.get("model")
        if (
            not isinstance(model, str)
            or not model
            or len(model) > 128
            or model.startswith("-")
            or any(c.isspace() or ord(c) < 32 for c in model)
        ):
            continue
        label = row.get("displayName")
        offers[model] = ModelOffer(
            model,
            label if isinstance(label, str) and label else model,
            row.get("isDefault") is True,
        )
    return list(offers.values())


def discover_models(project: Path) -> list[ModelOffer]:
    executable = find_codex()
    if not executable:
        raise CodexError("Run orchestrator setup before discovering Codex models.")
    try:
        process = subprocess.Popen(
            [executable, "app-server", "--listen", "stdio://"],
            cwd=project.resolve(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        raise CodexError("Could not start the local Codex model catalog.") from exc
    try:
        client = _CatalogClient(process)
        client.send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "codex_orchestrator", "version": "0.1.0"}},
            }
        )
        client.result(1)
        client.send({"method": "initialized"})
        rows, cursor = [], None
        for request_id in range(2, 7):
            params = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            client.send({"id": request_id, "method": "model/list", "params": params})
            result = client.result(request_id)
            page = result.get("data")
            if not isinstance(page, list):
                raise CodexError("The Codex model catalog returned invalid data.")
            rows.extend(page)
            cursor = result.get("nextCursor")
            if not cursor:
                offers = parse_offers(rows)
                if not offers:
                    raise CodexError("No selectable Codex models were reported.")
                return offers
            if not isinstance(cursor, str):
                raise CodexError("The Codex model catalog returned an invalid cursor.")
        raise CodexError("The model catalog exceeds the supported page limit.")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdin.close()
        process.stdout.close()


def suggested_policy(offers: list[ModelOffer]) -> ModelPolicy:
    # Use only actual picker-visible IDs, never synthesize a slug from a display name.
    families = {}
    for offer in offers:
        match = FAMILY.fullmatch(offer.id)
        if match:
            version = tuple(int(part) for part in match.group(1).split("."))
            family = match.group(2).lower()
            if family not in families or version > families[family][0]:
                families[family] = (version, offer.id)

    def choose(*preferences):
        return next((families[f][1] for f in preferences if f in families), None)

    return ModelPolicy(
        routine=choose("luna", "terra", "sol", "astra"),
        standard=choose("sol", "terra", "astra"),
        strong_reasoning=choose("astra", "sol"),
    )


def selected_policy(offers, *, routine=None, standard=None, strong=None):
    defaults = suggested_policy(offers)
    ids = {offer.id for offer in offers}
    for field, selected in (
        ("routine", routine),
        ("standard", standard),
        ("strong_reasoning", strong),
    ):
        if selected is None:
            continue
        if selected.casefold() == "default":
            setattr(defaults, field, None)
        elif selected in ids:
            setattr(defaults, field, selected)
        else:
            raise ValueError(f"Model {selected!r} is not in the Codex model catalog.")
    return defaults
