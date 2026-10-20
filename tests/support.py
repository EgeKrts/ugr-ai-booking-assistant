"""Shared helpers for database/tool tests: invented e-mails and a scripted extractor."""
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from ugr_booking.db import BookingRow, EmailRow, EventRow, IssueRow, make_engine
from ugr_booking.extractor import ExtractionError
from ugr_booking.orchestrator import DirectPlanner, Orchestrator
from ugr_booking.schemas import EmailRecord, ExtractedBooking, ExtractionResult, MessageIntent
from test_validation import BODY, GOOD, MASTER

CREATE = MessageIntent.CREATE_BOOKING
UPDATE = MessageIntent.UPDATE_BOOKING
CANCEL = MessageIntent.CANCEL_BOOKING
STATUS = MessageIntent.STATUS_INQUIRY
NON_BOOKING = MessageIntent.NON_BOOKING

BASE_TIME = datetime(2026, 10, 1, 9, 0)


def mail(number: int, body: str, suspicious: str = "") -> EmailRecord:
    email_id = f"EXAMPLE-{number:03d}"
    return EmailRecord(
        email_id=email_id, received_at=BASE_TIME + timedelta(hours=number), subject="Invented",
        sender="x@example.test", raw_body=body, clean_body=body, suspicious_text=suspicious,
        file_name=f"emails/{email_id}.txt")


def with_ref(ref: str, body: str = BODY) -> str:
    return f"Booking Ref: {ref}\n{body}"


def result(intent=CREATE, target=None, bookings=None) -> ExtractionResult:
    return ExtractionResult(intent=intent, target_booking_ref=target, bookings=bookings or [])


def booking(**changes) -> ExtractedBooking:
    return ExtractedBooking(**{**GOOD, **changes})


def partial(**fields) -> ExtractedBooking:
    return ExtractedBooking(**fields)


class ScriptedExtractor:
    """Returns the prepared result for an e-mail id, or raises a prepared error."""

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[str] = []

    def extract(self, email):
        self.calls.append(email.email_id)
        value = self.script[email.email_id]
        if isinstance(value, Exception):
            raise value
        return value


def make_orchestrator(script: dict, engine=None, planner=None):
    engine = engine or make_engine("sqlite://")
    extractor = ScriptedExtractor(script)
    return Orchestrator(engine, extractor, MASTER, planner or DirectPlanner()), engine, extractor


def rows(engine, model):
    with Session(engine) as session:
        return list(session.scalars(select(model).order_by(model.id if hasattr(model, "id") else model.email_id)))


def count(engine, model) -> int:
    return len(rows(engine, model))


def event_types(engine) -> list[str]:
    return [e.event_type for e in rows(engine, EventRow)]


def tool_failure() -> ExtractionError:
    return ExtractionError("LLM_UNAVAILABLE", "scripted outage")


__all__ = [name for name in dir() if not name.startswith("_")] + ["BookingRow", "EmailRow", "IssueRow"]
