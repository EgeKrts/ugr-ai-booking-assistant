"""Load customer/port references and check the suggested booking contract."""
from dataclasses import dataclass
import re
from typing import Iterable
from pathlib import Path
from pydantic import TypeAdapter, ValidationError
from .dataset import DatasetError, DatasetSource, load_manifest
from .normalize import fold
from .schemas import BookingDraft, Customer, ManifestEntry, MessageIntent, Port, ProcessingStatus

@dataclass(frozen=True)
class DatasetBundle:
    manifest: tuple[ManifestEntry, ...]
    customers: tuple[Customer, ...]
    ports: tuple[Port, ...]
    kind: str

    def summary(self) -> dict:
        first, last = self.manifest[0], self.manifest[-1]
        return {
            "dataset_kind": self.kind,
            "email_count": len(self.manifest),
            "unique_email_ids": len({entry.email_id for entry in self.manifest}),
            "matched_email_files": len(self.manifest),
            "customer_count": len(self.customers),
            "port_count": len(self.ports),
            "booking_schema_fields": len(BookingDraft.model_fields),
            "message_intents": [item.value for item in MessageIntent],
            "processing_statuses": [item.value for item in ProcessingStatus],
            "first_email": first.email_id,
            "first_received_at": first.received_at.isoformat(),
            "last_email": last.email_id,
            "last_received_at": last.received_at.isoformat(),
        }

def expected_booking_contract() -> dict[str, str]:
    strings = (
        "booking_ref", "customer", "pol", "pod", "cargo_description",
        "vessel", "voyage", "rate_text", "contact_name", "contact_email",
    )
    numbers = ("weight_kg_per_unit", "length_m", "width_m", "height_m")
    expected = {name: "string|null" for name in strings}
    expected.update({name: "number|null" for name in numbers})
    expected.update({
        "message_intent": "|".join(item.value for item in MessageIntent),
        "processing_status": "|".join(item.value for item in ProcessingStatus),
        "quantity": "integer|null",
        "vin_list": "array[string]",
        "requested_date": "YYYY-MM-DD|null",
        "source_email_id": "string",
    })
    return expected

def validate_booking_contract(raw: dict) -> None:
    expected = expected_booking_contract()
    if set(expected) != set(BookingDraft.model_fields):
        raise DatasetError("Internal booking model and contract fields differ")
    if raw != expected:
        raise DatasetError("Supplied booking schema differs from the supported field/type contract")

def _master(source, name, model, count, identity):
    raw = source.read_json(name)
    if not isinstance(raw, list) or len(raw) != count:
        raise DatasetError(f"Expected {count} records in {name}")
    try:
        items = TypeAdapter(list[model]).validate_python(raw)
    except ValidationError as exc:
        raise DatasetError(f"Invalid master-data structure in {name}") from exc
    ids = [getattr(item, identity) for item in items]
    if len(ids) != len(set(ids)):
        raise DatasetError(f"Duplicate master identifier in {name}")
    return tuple(items)

def load_dataset(path: Path) -> DatasetBundle:
    # These counts are the supplied dataset contract, not hardcoded dashboard metrics.
    with DatasetSource(path) as source:
        manifest = load_manifest(source, expected_count=34)
        customers = _master(source, "master_data/customers.json", Customer, 4, "customer_id")
        ports = _master(source, "master_data/ports.json", Port, 12, "port_code")
        validate_booking_contract(source.read_json("master_data/booking_schema.json"))
        return DatasetBundle(manifest, customers, ports, source.kind)


@dataclass(frozen=True)
class MatchResult:
    """`value` is the canonical name (customer) or port code; only set when status == matched."""
    status: str                      # matched | unknown | ambiguous
    value: str | None = None
    candidates: tuple[str, ...] = ()


_SPLIT = re.compile(r"\s*(?:/|,|;|&|\bor\b|\band\b|\bveya\b)\s*", re.IGNORECASE)


class MasterIndex:
    """Exact lookup that ignores capitalisation and accents; no fuzzy guessing."""

    def __init__(self, customers: Iterable[Customer], ports: Iterable[Port]):
        self._customers = self._build((c.name, [c.name, *c.aliases]) for c in customers)
        self._ports = self._build((p.port_code, [p.port_code, p.name, *p.aliases]) for p in ports)

    @staticmethod
    def _build(items) -> dict[str, set[str]]:
        index: dict[str, set[str]] = {}
        for value, names in items:
            for name in names:
                index.setdefault(fold(name), set()).add(value)
        return index

    @classmethod
    def from_dataset(cls, dataset: DatasetBundle) -> "MasterIndex":
        return cls(dataset.customers, dataset.ports)

    @staticmethod
    def _lookup(index: dict[str, set[str]], text: str) -> MatchResult:
        hits = index.get(fold(text), set())
        if len(hits) == 1:
            return MatchResult("matched", next(iter(hits)), (next(iter(hits)),))
        if len(hits) > 1:
            return MatchResult("ambiguous", None, tuple(sorted(hits)))
        return MatchResult("unknown")

    def match_customer(self, text: str) -> MatchResult:
        return self._lookup(self._customers, text)

    def match_port(self, text: str) -> MatchResult:
        whole = self._lookup(self._ports, text)
        if whole.status != "unknown":
            return whole
        parts = [part for part in _SPLIT.split(text) if part.strip()]
        if len(parts) > 1:
            # "JEDDAH / YANBU" names two ports: the e-mail does not say which one.
            found = sorted({r.value for r in (self._lookup(self._ports, p) for p in parts) if r.value})
            if found:
                return MatchResult("ambiguous", None, tuple(found))
        return whole
