"""Read-only, parameterised queries over the booking database.

The dashboard and the chatbot both go through these functions, so the same
filter always returns the same bookings. No caller builds SQL from user text.

Definitions used everywhere:
- "latest" means the booking whose source e-mail arrived most recently (ties: higher id).
  Processing time is not used because a batch writes everything within seconds.
- Cancelled bookings are bookings: they count in totals and customer figures unless
  a status filter excludes them.
- "Needs review" means status VALIDATION_REQUIRED.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import date, datetime
import re
from typing import Any
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from .db import BookingRow, EmailRow, EventRow, IssueRow

UNKNOWN_LABEL = "(unknown)"
BREAKDOWNS = ("status", "customer", "pol", "pod", "route")
ORDERS = ("latest", "oldest", "requested_date", "customer")
_BOOKING_ID = re.compile(r"^\s*bkg-?0*(\d+)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class BookingFilter:
    customers: tuple[str, ...] = ()
    pols: tuple[str, ...] = ()
    pods: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    text: str = ""                 # booking ref, BKG-id, source e-mail, customer or cargo
    date_from: date | None = None  # requested date range; bookings without a date are then excluded
    date_to: date | None = None
    ports_either: tuple[str, ...] = ()   # a port that may be either the loading or the discharge port

    @property
    def is_empty(self) -> bool:
        return self == BookingFilter()


@dataclass(frozen=True)
class BookingView:
    id: int
    display_id: str
    external_ref: str | None
    customer: str | None
    pol: str | None
    pod: str | None
    quantity: int | None
    requested_date: date | None
    status: str
    source_email_id: str
    received_at: datetime
    open_issues: int

    @property
    def needs_review(self) -> bool:
        return self.status == "VALIDATION_REQUIRED"


@dataclass
class BookingDetail:
    view: BookingView
    fields: dict[str, Any]
    issues: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    source_email: dict = field(default_factory=dict)


# ----------------------------------------------------------------- filtering

def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _in_or_null(column, values: tuple[str, ...]):
    named = [v for v in values if v != UNKNOWN_LABEL]
    clauses = []
    if named:
        clauses.append(column.in_(named))
    if UNKNOWN_LABEL in values:
        clauses.append(column.is_(None))
    return or_(*clauses)


def conditions(flt: BookingFilter) -> list:
    """WHERE clauses for a filter; every criterion narrows the result (AND)."""
    out = []
    if flt.customers:
        out.append(_in_or_null(BookingRow.customer, flt.customers))
    if flt.pols:
        out.append(_in_or_null(BookingRow.pol, flt.pols))
    if flt.pods:
        out.append(_in_or_null(BookingRow.pod, flt.pods))
    if flt.ports_either:
        out.append(or_(BookingRow.pol.in_(flt.ports_either), BookingRow.pod.in_(flt.ports_either)))
    if flt.statuses:
        out.append(BookingRow.status.in_(flt.statuses))
    if flt.date_from:
        out.append(BookingRow.requested_date >= flt.date_from)
    if flt.date_to:
        out.append(BookingRow.requested_date <= flt.date_to)
    text = flt.text.strip()
    if text:
        match = _BOOKING_ID.match(text)
        if match:
            out.append(BookingRow.id == int(match.group(1)))
        else:
            pattern = f"%{_like_escape(text)}%"
            out.append(or_(*(col.ilike(pattern, escape="\\") for col in (
                BookingRow.external_ref, BookingRow.customer, BookingRow.cargo_description,
                BookingRow.source_email_id))))
    return out


def _applies_to_booking():
    """Issues of the booking itself, plus e-mail level issues of its source e-mail
    (for example a suspected prompt injection), which hold back every booking from that e-mail."""
    return or_(IssueRow.booking_id == BookingRow.id,
               and_(IssueRow.booking_id.is_(None), IssueRow.email_id == BookingRow.source_email_id))


def _views_query(flt: BookingFilter):
    open_errors = (select(func.count()).select_from(IssueRow)
                   .where(IssueRow.review_status == "OPEN", IssueRow.severity == "ERROR", _applies_to_booking())
                   .correlate(BookingRow).scalar_subquery())
    return (select(BookingRow, EmailRow.received_at, open_errors)
            .join(EmailRow, EmailRow.email_id == BookingRow.source_email_id)
            .where(*conditions(flt)))


def _view(row: BookingRow, received_at: datetime, open_issues: int) -> BookingView:
    return BookingView(
        id=row.id, display_id=row.display_id, external_ref=row.external_ref, customer=row.customer,
        pol=row.pol, pod=row.pod, quantity=row.quantity, requested_date=row.requested_date,
        status=row.status, source_email_id=row.source_email_id, received_at=received_at, open_issues=open_issues)


# ------------------------------------------------------------------- queries

def search_bookings(session: Session, flt: BookingFilter | None = None, *, order: str = "latest",
                    limit: int | None = None) -> list[BookingView]:
    flt = flt or BookingFilter()
    if order not in ORDERS:
        raise ValueError(f"order must be one of {ORDERS}")
    query = _views_query(flt)
    if order == "latest":
        query = query.order_by(EmailRow.received_at.desc(), BookingRow.id.desc())
    elif order == "oldest":
        query = query.order_by(EmailRow.received_at.asc(), BookingRow.id.asc())
    elif order == "requested_date":
        query = query.order_by(BookingRow.requested_date.is_(None), BookingRow.requested_date, BookingRow.id)
    else:
        query = query.order_by(BookingRow.customer.is_(None), BookingRow.customer, BookingRow.id)
    if limit is not None:
        query = query.limit(max(0, limit))
    return [_view(*row) for row in session.execute(query)]


def count_bookings(session: Session, flt: BookingFilter | None = None) -> int:
    query = select(func.count()).select_from(BookingRow).where(*conditions(flt or BookingFilter()))
    return session.scalar(query) or 0


def latest_bookings(session: Session, n: int = 3, flt: BookingFilter | None = None) -> list[BookingView]:
    return search_bookings(session, flt, order="latest", limit=n)


def bookings_requiring_review(session: Session, flt: BookingFilter | None = None) -> list[BookingView]:
    only_review = replace(flt or BookingFilter(), statuses=("VALIDATION_REQUIRED",))
    return search_bookings(session, only_review)


def count_by(session: Session, dimension: str, flt: BookingFilter | None = None) -> list[tuple[str, int]]:
    """(label, count) pairs, largest first; a missing value is labelled "(unknown)"."""
    if dimension not in BREAKDOWNS:
        raise ValueError(f"dimension must be one of {BREAKDOWNS}")
    if dimension == "route":
        label = (func.coalesce(BookingRow.pol, "?") + " > " + func.coalesce(BookingRow.pod, "?"))
    else:
        label = func.coalesce(getattr(BookingRow, dimension), UNKNOWN_LABEL)
    query = (select(label, func.count()).where(*conditions(flt or BookingFilter()))
             .group_by(label).order_by(func.count().desc(), label))
    return [(str(name), int(n)) for name, n in session.execute(query)]


def filter_options(session: Session) -> dict[str, list[str]]:
    """Distinct values that exist in the data, for building filter widgets."""
    def distinct(column) -> list[str]:
        values = [v for v in session.scalars(select(column).distinct().order_by(column)) if v]
        has_null = session.scalar(select(func.count()).select_from(BookingRow).where(column.is_(None))) or 0
        return values + ([UNKNOWN_LABEL] if has_null else [])
    return {"customers": distinct(BookingRow.customer), "pols": distinct(BookingRow.pol),
            "pods": distinct(BookingRow.pod), "statuses": distinct(BookingRow.status)}


def email_overview(session: Session) -> dict[str, Any]:
    """E-mail level figures, kept apart from booking counts (a non-booking e-mail is not a booking)."""
    by_intent = dict(session.execute(select(func.coalesce(EmailRow.intent, "NONE"), func.count()).group_by(EmailRow.intent)).all())
    by_status = dict(session.execute(select(EmailRow.status, func.count()).group_by(EmailRow.status)).all())
    unresolved = session.scalar(select(func.count()).select_from(EventRow)
                                .where(EventRow.event_type == "FAILED_ACTION")) or 0
    open_errors = session.scalar(select(func.count()).select_from(IssueRow)
                                 .where(IssueRow.review_status == "OPEN", IssueRow.severity == "ERROR")) or 0
    return {"emails_total": sum(by_intent.values()), "by_intent": by_intent, "by_status": by_status,
            "failed_actions": unresolved, "open_error_issues": open_errors}


def failed_actions(session: Session) -> list[dict]:
    """UPDATE/CANCEL requests that could not be applied, and conflicting repeats."""
    query = (select(EventRow, EmailRow).join(EmailRow, EmailRow.email_id == EventRow.source_email_id)
             .where(EventRow.event_type == "FAILED_ACTION").order_by(EmailRow.received_at, EventRow.id))
    out = []
    for event, email in session.execute(query):
        out.append({"email_id": email.email_id, "received_at": email.received_at, "intent": email.intent,
                    "subject": email.subject, "reason": event.reason,
                    "booking": f"BKG-{event.booking_id:05d}" if event.booking_id else None})
    return out


def get_booking_detail(session: Session, booking_id: int) -> BookingDetail | None:
    row = session.execute(_views_query(BookingFilter(text=f"BKG-{booking_id}"))).first()
    if row is None:
        return None
    booking, received_at, open_issues = row
    fields = {
        "external_ref": booking.external_ref, "customer": booking.customer, "pol": booking.pol, "pod": booking.pod,
        "cargo_description": booking.cargo_description, "quantity": booking.quantity,
        "requested_date": booking.requested_date, "weight_kg_per_unit": booking.weight_kg_per_unit,
        "dimensions_m": booking.dimensions_m, "vin_list": booking.vin_list, "vessel": booking.vessel,
        "voyage": booking.voyage, "rate_text": booking.rate_text, "contact_name": booking.contact_name,
        "contact_email": booking.contact_email,
    }
    issues = [{"scope": "booking" if i.booking_id else "e-mail", "code": i.code, "field": i.field,
               "severity": i.severity, "message": i.message, "review_status": i.review_status, "email_id": i.email_id}
              for i in session.scalars(select(IssueRow).where(
                  or_(IssueRow.booking_id == booking_id,
                      and_(IssueRow.booking_id.is_(None), IssueRow.email_id == booking.source_email_id))
              ).order_by(IssueRow.id))]
    events = [{"event_type": e.event_type, "email_id": e.source_email_id, "reason": e.reason, "details": e.details,
               "at": e.created_at}
              for e in session.scalars(select(EventRow).where(EventRow.booking_id == booking_id).order_by(EventRow.id))]
    email = session.get(EmailRow, booking.source_email_id)
    source = {} if email is None else {
        "email_id": email.email_id, "subject": email.subject, "sender": email.sender, "received_at": email.received_at,
        "intent": email.intent, "status": email.status, "outcome": email.outcome, "text": email.clean_body}
    return BookingDetail(_view(booking, received_at, open_issues), fields, issues, events, source)


def open_issue_codes(session: Session) -> dict[int, list[str]]:
    """Open ERROR issue codes per booking (own and e-mail level), i.e. why a booking needs attention."""
    own: dict[int, list[str]] = {}
    by_email: dict[str, list[str]] = {}
    query = (select(IssueRow.booking_id, IssueRow.email_id, IssueRow.code)
             .where(IssueRow.review_status == "OPEN", IssueRow.severity == "ERROR").order_by(IssueRow.id))
    for booking_id, email_id, code in session.execute(query):
        target = own.setdefault(booking_id, []) if booking_id else by_email.setdefault(email_id, [])
        if code not in target:
            target.append(code)
    out: dict[int, list[str]] = {}
    for booking_id, email_id in session.execute(select(BookingRow.id, BookingRow.source_email_id)):
        codes = own.get(booking_id, []) + [c for c in by_email.get(email_id, []) if c not in own.get(booking_id, [])]
        if codes:
            out[booking_id] = codes
    return out
