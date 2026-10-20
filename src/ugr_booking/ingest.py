"""Read every manifest e-mail into an EmailRecord; one bad file never stops the batch."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from pydantic import ValidationError
from .dataset import DatasetSource, load_manifest
from .preprocess import clean_body, has_suspect_encoding
from .schemas import EmailRecord, ManifestEntry

_SEPARATOR = re.compile(r"(?m)^-{5,}[ \t]*(?:\r?\n|$)")
_HEADER = re.compile(r"^([A-Za-z][A-Za-z-]*):[ \t]*(.*)$")
_DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")


class EmailParseError(ValueError):
    """A controlled failure while reading an e-mail's text structure."""


@dataclass(frozen=True)
class EmailFailure:
    email_id: str
    file_name: str
    error: str


@dataclass(frozen=True)
class IngestReport:
    records: tuple[EmailRecord, ...]
    failures: tuple[EmailFailure, ...]
    manifest_total: int

    def summary(self) -> dict:
        warning_counts = Counter(code for record in self.records for code in record.warnings)
        return {
            "email_total": self.manifest_total,
            "parsed": len(self.records),
            "failed": len(self.failures),
            "unaccounted": self.manifest_total - len(self.records) - len(self.failures),
            "html_cleaned": sum(record.was_html for record in self.records),
            "with_quoted_history": sum(bool(record.quoted_body) for record in self.records),
            "warning_counts": dict(sorted(warning_counts.items())),
            # Identifiers and codes only: e-mail bodies are never printed.
            "emails": [
                {"email_id": record.email_id, "warnings": list(record.warnings)}
                for record in self.records
            ] + [
                {"email_id": failure.email_id, "error": failure.error}
                for failure in self.failures
            ],
        }


def decode_text(raw: bytes) -> tuple[str, list[str]]:
    """Decode UTF-8; on invalid bytes keep going with replacement characters and say so."""
    try:
        return raw.decode("utf-8-sig"), []
    except UnicodeDecodeError:
        return raw.decode("utf-8-sig", errors="replace"), ["INVALID_UTF8_REPLACED"]


def _parse_date(value: str) -> datetime | None:
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            pass
    return None


def parse_email_text(text: str, entry: ManifestEntry, warnings: list[str] | None = None) -> EmailRecord:
    """Parse headers and body against the manifest entry; differences are reported, not fixed."""
    warnings = list(warnings or [])
    separator = _SEPARATOR.search(text)
    if separator is None:
        raise EmailParseError("Could not find the dashed header/body separator")

    headers: dict[str, str] = {}
    for line in text[:separator.start()].splitlines():
        match = _HEADER.match(line.strip())
        if match:
            headers[match.group(1).casefold()] = match.group(2).strip()

    # The manifest is the system of record for identity and ordering; the
    # header copy is compared so that mismatches stay visible.
    header_id = headers.get("email-id")
    if not header_id:
        warnings.append("MISSING_EMAIL_ID_HEADER")
    elif header_id != entry.email_id:
        warnings.append("EMAIL_ID_MISMATCH")

    date_text = headers.get("date")
    header_date = _parse_date(date_text) if date_text else None
    if not date_text:
        warnings.append("MISSING_DATE_HEADER")
    elif header_date is None:
        warnings.append("UNPARSEABLE_DATE_HEADER")
    elif header_date != entry.received_at:
        warnings.append("DATE_MISMATCH")

    subject = headers.get("subject")
    if subject is None:
        warnings.append("MISSING_SUBJECT_HEADER")
        subject = entry.subject

    sender = headers.get("from") or None
    if sender is None:
        warnings.append("MISSING_SENDER")

    raw_body = text[separator.end():]
    cleaned = clean_body(raw_body)
    if not cleaned.current_text:
        warnings.append("EMPTY_BODY")
    if has_suspect_encoding(text):
        warnings.append("SUSPECT_ENCODING")

    return EmailRecord(
        email_id=entry.email_id,
        received_at=entry.received_at,
        subject=subject,
        sender=sender,
        raw_body=raw_body,
        clean_body=cleaned.current_text,
        quoted_body=cleaned.quoted_text,
        was_html=cleaned.was_html,
        file_name=entry.file,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def read_emails(source: DatasetSource, manifest: tuple[ManifestEntry, ...]) -> IngestReport:
    """Process entries in the given (chronological) order and account for every one."""
    records: list[EmailRecord] = []
    failures: list[EmailFailure] = []
    for entry in manifest:
        try:
            text, warnings = decode_text(source.read_bytes(entry.file))
            records.append(parse_email_text(text, entry, warnings))
        except (EmailParseError, ValidationError, ValueError, OSError) as exc:
            failures.append(EmailFailure(entry.email_id, entry.file, f"{type(exc).__name__}: {exc}"))
    return IngestReport(tuple(records), tuple(failures), len(manifest))


def read_dataset_emails(path: Path, expected_count: int | None = 34) -> IngestReport:
    with DatasetSource(path) as source:
        return read_emails(source, load_manifest(source, expected_count=expected_count))
