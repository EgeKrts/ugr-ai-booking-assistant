"""All reads and writes of booking data. Callers own the transaction (session.begin())."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .db import BookingRow, EmailRow, EventRow, IssueRow
from .schemas import BookingDraft, EmailRecord, ExtractedBooking, ValidationIssue

# Values an UPDATE may change. Reference and status are never changed this way.
UPDATABLE_FIELDS = (
    "customer", "pol", "pod", "cargo_description", "quantity", "vin_list", "weight_kg_per_unit",
    "dimensions_m", "requested_date", "vessel", "voyage", "rate_text", "contact_name", "contact_email",
)
# Fields compared when deciding whether a repeated CREATE says the same thing.
COMPARED_FIELDS = (
    "customer", "pol", "pod", "cargo_description", "quantity", "vin_list", "weight_kg_per_unit",
    "requested_date", "dimensions_m",
)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


class Repository:
    def __init__(self, session: Session):
        self.session = session

    # ------------------------------------------------------------- e-mails
    def get_email(self, email_id: str) -> EmailRow | None:
        return self.session.get(EmailRow, email_id)

    def save_email(self, email: EmailRecord, *, intent: str | None, status: str, outcome: str | None = None,
                   error: str | None = None, extraction: dict | None = None) -> EmailRow:
        row = self.get_email(email.email_id) or EmailRow(email_id=email.email_id)
        row.received_at, row.subject, row.sender = email.received_at, email.subject, email.sender
        row.file_name, row.raw_body, row.clean_body = email.file_name, email.raw_body, email.clean_body
        row.intent, row.status, row.outcome, row.error = intent, status, outcome, error
        row.warnings, row.extraction, row.processed_at = list(email.warnings), extraction, _now()
        self.session.add(row)
        self.session.flush()
        return row

    # ------------------------------------------------------------ bookings
    def get_booking(self, booking_id: int) -> BookingRow | None:
        return self.session.get(BookingRow, booking_id)

    def booking_by_ref(self, ref: str) -> BookingRow | None:
        return self.session.scalar(select(BookingRow).where(BookingRow.external_ref == ref))

    def booking_with_vins(self, vins: list[str]) -> BookingRow | None:
        """A booking carrying exactly this non-empty set of VINs (VINs identify vehicles)."""
        wanted = set(vins)
        if not wanted:
            return None
        for row in self.session.scalars(select(BookingRow).order_by(BookingRow.id)):
            if set(row.vin_list or []) == wanted:
                return row
        return None

    def add_booking(self, draft: BookingDraft, raw: ExtractedBooking) -> BookingRow:
        dims = None
        if draft.length_m is not None and draft.width_m is not None and draft.height_m is not None:
            dims = {"length": draft.length_m, "width": draft.width_m, "height": draft.height_m}
        now = _now()
        row = BookingRow(
            external_ref=draft.booking_ref, customer=draft.customer, pol=draft.pol, pod=draft.pod,
            cargo_description=draft.cargo_description, quantity=draft.quantity,
            requested_date=draft.requested_date, weight_kg_per_unit=draft.weight_kg_per_unit,
            dimensions_m=dims, vin_list=list(draft.vin_list), vessel=draft.vessel, voyage=draft.voyage,
            rate_text=draft.rate_text, contact_name=draft.contact_name, contact_email=draft.contact_email,
            status=draft.processing_status.value, raw=raw.model_dump(mode="json"),
            source_email_id=draft.source_email_id, created_at=now, updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def apply_changes(self, row: BookingRow, changes: dict[str, Any]) -> dict[str, list]:
        """Set the given fields and return {field: [old, new]} for the audit trail."""
        diff: dict[str, list] = {}
        for field, value in changes.items():
            if field not in UPDATABLE_FIELDS:
                raise ValueError(f"Field {field!r} cannot be updated")
            old = getattr(row, field)
            if old != value:
                diff[field] = [_jsonable(old), _jsonable(value)]
                setattr(row, field, value)
        row.updated_at = _now()
        self.session.flush()
        return diff

    def set_status(self, row: BookingRow, status: str) -> None:
        row.status = status
        row.updated_at = _now()
        self.session.flush()

    # -------------------------------------------------- events and issues
    def add_event(self, event_type: str, source_email_id: str, *, booking_id: int | None = None,
                  reason: str | None = None, details: dict | None = None) -> EventRow:
        row = EventRow(booking_id=booking_id, event_type=event_type, source_email_id=source_email_id,
                       created_at=_now(), reason=reason, details=details)
        self.session.add(row)
        self.session.flush()
        return row

    def add_issue(self, email_id: str, issue: ValidationIssue, booking_id: int | None) -> IssueRow:
        row = IssueRow(email_id=email_id, booking_id=booking_id, booking_index=issue.booking_index,
                       code=issue.code, field=issue.field, severity=issue.severity.value,
                       message=issue.message, review_status="OPEN", created_at=_now())
        self.session.add(row)
        self.session.flush()
        return row

    # ------------------------------------------------------------- counts
    def count(self, model) -> int:
        return self.session.scalar(select(func.count()).select_from(model)) or 0


def _jsonable(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def comparable(row_or_draft: Any) -> dict[str, Any]:
    """Normalised view of the compared fields, for duplicate/conflict decisions."""
    if isinstance(row_or_draft, BookingRow):
        get = lambda name: getattr(row_or_draft, name)
    else:
        def get(name):
            if name == "dimensions_m":
                d = row_or_draft
                if d.length_m is None or d.width_m is None or d.height_m is None:
                    return None
                return {"length": d.length_m, "width": d.width_m, "height": d.height_m}
            return getattr(row_or_draft, name)
    view = {name: get(name) for name in COMPARED_FIELDS}
    view["vin_list"] = sorted(view["vin_list"] or []) or None
    return view
