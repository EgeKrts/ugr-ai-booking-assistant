"""Record model output once, replay it later without the model.

Replay is for regression tests and for an offline demo. It is never presented as live AI:
the CLI prints a banner and the recording carries the model name and the prompt/schema hashes,
so a recording made with an older prompt is reported as stale.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from pydantic import ValidationError
from .extractor import SYSTEM_PROMPT, ExtractionError, Extractor, OllamaClient
from .schemas import EmailRecord, ExtractionResult

META_FILE = "_meta.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def current_fingerprint() -> dict[str, str]:
    """What the recording depends on: the extraction prompt and the result schema."""
    return {"prompt_sha256": _sha(SYSTEM_PROMPT),
            "schema_sha256": _sha(json.dumps(ExtractionResult.model_json_schema(), sort_keys=True))}


@dataclass
class RecordingSummary:
    recorded: int
    errors: int
    seconds: float


def record_extractions(emails: list[EmailRecord], extractor: Extractor, directory: Path, on_email=None) -> RecordingSummary:
    """Run the model on every e-mail and store each result (or its error) as <email_id>.json."""
    directory.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    errors = 0
    started = time.perf_counter()
    for email in sorted(emails, key=lambda e: (e.received_at, e.email_id)):
        begun = time.perf_counter()
        try:
            payload = {"result": extractor.extract(email).model_dump(mode="json")}
        except ExtractionError as exc:
            payload = {"error": str(exc)}
            errors += 1
        timings[email.email_id] = round(time.perf_counter() - begun, 2)
        (directory / f"{email.email_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        if on_email:
            on_email(email.email_id, payload, timings[email.email_id])
    total = round(time.perf_counter() - started, 1)
    client = getattr(extractor, "client", None)
    meta = {"recorded_at": datetime.now().replace(microsecond=0).isoformat(),
            "model": getattr(client, "model", "unknown"), "emails": len(timings), "errors": errors,
            "total_seconds": total, "seconds_per_email": timings, **current_fingerprint()}
    (directory / META_FILE).write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return RecordingSummary(len(timings) - errors, errors, total)


class RecordedExtractor:
    """Drop-in for Extractor that reads a recording; raises exactly what the recorded run raised."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        meta_path = self.directory / META_FILE
        self.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}

    @property
    def stale(self) -> bool:
        """True when the prompt or schema changed after the recording was made."""
        current = current_fingerprint()
        return any(self.meta.get(key) != value for key, value in current.items())

    def describe(self) -> str:
        return (f"replay of recorded output from {self.meta.get('recorded_at', 'unknown time')} "
                f"(model {self.meta.get('model', 'unknown')})" + ("; STALE: prompt or schema changed since" if self.stale else ""))

    def extract(self, email: EmailRecord) -> ExtractionResult:
        path = self.directory / f"{email.email_id}.json"
        if not path.is_file():
            raise ExtractionError("RECORDING_MISSING", f"No recorded extraction for {email.email_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if "error" in data:
            code, _, message = data["error"].partition(": ")
            raise ExtractionError(code or "RECORDED_ERROR", message or data["error"])
        try:
            return ExtractionResult.model_validate(data["result"])
        except ValidationError as exc:
            raise ExtractionError("RECORDING_INVALID", f"Recording for {email.email_id} no longer matches the schema") from exc
