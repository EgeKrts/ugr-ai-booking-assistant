"""Read metadata from a directory or ZIP without extracting or executing it."""
from __future__ import annotations
from datetime import datetime
import json
from pathlib import Path, PurePosixPath
import zipfile
from pydantic import ValidationError
from .schemas import ManifestEntry

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 10000

class DatasetError(ValueError):
    """A controlled package-validation failure."""

def safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise DatasetError("Expected a non-empty relative POSIX file path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in value.split("/") or path.as_posix() == ".":
        raise DatasetError("Path escapes the dataset")
    return path.as_posix()

class DatasetSource:
    def __init__(self, path: Path):
        self.root = path.resolve()
        self.archive: zipfile.ZipFile | None = None
        self.kind = "directory"

    def __enter__(self):
        if self.root.is_dir():
            return self
        if not self.root.is_file():
            raise DatasetError("Dataset path does not exist")
        try:
            self.archive = zipfile.ZipFile(self.root)
            self.kind = "zip"
            items = self.archive.infolist()
            if len(items) > MAX_ENTRIES:
                raise DatasetError("ZIP contains too many entries")
            seen = set()
            for item in items:
                name = safe_relative_path(item.filename)
                if name in seen:
                    raise DatasetError("ZIP contains duplicate member paths")
                seen.add(name)
            return self
        except Exception:
            if self.archive:
                self.archive.close()
            raise

    def __exit__(self, *args):
        if self.archive:
            self.archive.close()

    def check_file(self, name: str):
        name = safe_relative_path(name)
        if self.archive:
            try:
                item = self.archive.getinfo(name)
            except KeyError as exc:
                raise DatasetError(f"Referenced file is missing: {name}") from exc
            if item.is_dir() or item.file_size > MAX_FILE_BYTES:
                raise DatasetError(f"Not a file or exceeds 2 MiB: {name}")
            return item
        target = (self.root / name).resolve()
        if not target.is_relative_to(self.root):
            raise DatasetError("Resolved path escapes the dataset")
        if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
            raise DatasetError(f"Missing file or exceeds 2 MiB: {name}")
        return target

    def read_json(self, name: str):
        item = self.check_file(name)
        try:
            if self.archive:
                with self.archive.open(item) as stream:
                    raw = stream.read(MAX_FILE_BYTES + 1)
            else:
                with item.open("rb") as stream:
                    raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise DatasetError(f"File exceeds 2 MiB: {name}")
            return json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DatasetError(f"Invalid UTF-8 JSON in {name}") from exc


    def read_bytes(self, name: str) -> bytes:
        """Read a bounded file without decoding; callers decide how to handle bad text."""
        item = self.check_file(name)
        if self.archive:
            with self.archive.open(item) as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        else:
            with item.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise DatasetError(f"File exceeds 2 MiB: {name}")
        return raw

def load_manifest(source: DatasetSource, expected_count: int | None = None) -> tuple[ManifestEntry, ...]:
    raw = source.read_json("manifest.json")
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES:
        raise DatasetError("Manifest must be a bounded list")
    if expected_count is not None and len(raw) != expected_count:
        raise DatasetError(f"Expected {expected_count} manifest entries, found {len(raw)}")
    entries = []
    identifiers, paths = set(), set()
    for index, item in enumerate(raw):
        try:
            entry = ManifestEntry.model_validate(item)
        except ValidationError as exc:
            issues = "; ".join(f"{'.'.join(map(str,e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False))
            raise DatasetError(f"Manifest entry {index}: {issues}") from exc
        path = safe_relative_path(entry.file)
        if entry.email_id in identifiers:
            raise DatasetError("Manifest contains a duplicate e-mail identifier")
        if path.casefold() in paths:
            raise DatasetError("Manifest references the same file more than once")
        identifiers.add(entry.email_id)
        paths.add(path.casefold())
        source.check_file(path)
        entries.append(entry)
    # No timezone is assigned to the supplied naive local timestamps.
    return tuple(sorted(entries, key=lambda entry: (entry.received_at, entry.email_id)))
