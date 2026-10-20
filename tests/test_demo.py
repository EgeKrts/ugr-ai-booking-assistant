"""The follow-up demo must stay reproducible: a current recording, and the same outcomes when replayed."""
from datetime import date, datetime
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from ugr_booking.config import get_dataset_path
from ugr_booking.db import BookingRow, EmailRow, EventRow, make_engine
from ugr_booking.ingest import read_inbox_emails
from ugr_booking.master_data import MasterIndex, load_dataset
from ugr_booking.orchestrator import DirectPlanner, Orchestrator
from ugr_booking.recorded import RecordedExtractor

ROOT = Path(__file__).resolve().parent.parent
INBOX, RECORDING = ROOT / "examples" / "demo_inbox", ROOT / "examples" / "demo_recording"


def test_the_demo_recording_covers_the_inbox_and_is_not_stale():
    recording = RecordedExtractor(RECORDING)
    ids = {e.email_id for e in read_inbox_emails(INBOX).records}
    assert ids == {f"DEMO-00{i}" for i in range(1, 7)} and recording.meta["errors"] == 0
    for email in read_inbox_emails(INBOX).records:
        recording.extract(email)                      # every e-mail has a valid recorded result
    # Changing the prompt or the schema makes this fail on purpose: re-record with
    #   python -m ugr_booking.cli record-extractions --inbox examples/demo_inbox --out examples/demo_recording
    assert not recording.stale, recording.describe()


@pytest.fixture(scope="module")
def replayed():
    path = get_dataset_path()
    if not path.exists():
        pytest.skip("the dataset (master data) is not available locally")
    engine = make_engine("sqlite://")
    with Session(engine) as session, session.begin():
        session.add(EmailRow(email_id="SEED", received_at=datetime(2026, 10, 1), subject="seed", file_name="seed", raw_body="", clean_body="",
                             status="PROCESSED", processed_at=datetime(2026, 10, 1)))
        session.flush()
        for ref, customer, pol, pod, quantity, day, vins in (
                ("BOOKINGREF-A400", "ABC Shipping", "TRIZM", "ITLIV", 3, date(2026, 11, 10), ["V1", "V2", "V3"]),
                ("BOOKINGREF-D200", "DEF Automotive", "ESBCN", "SAYNB", 3, date(2026, 11, 20), ["V4", "V5", "V6"]),
                ("XYZ-REF-2026-1109", "XYZ Lojistik", "TRIZM", "SAYNB", 2, date(2026, 11, 18), ["V7", "V8"])):
            session.add(BookingRow(external_ref=ref, customer=customer, pol=pol, pod=pod, quantity=quantity, requested_date=day, vin_list=vins,
                                   status="READY", source_email_id="SEED", created_at=datetime(2026, 10, 1), updated_at=datetime(2026, 10, 1)))
    master = MasterIndex.from_dataset(load_dataset(path))
    report = Orchestrator(engine, RecordedExtractor(RECORDING), master, DirectPlanner()).ingest(read_inbox_emails(INBOX).records)
    return engine, report


def booking(engine, ref):
    with Session(engine) as session:
        return session.scalar(select(BookingRow).where(BookingRow.external_ref == ref))


def test_each_follow_up_has_the_expected_outcome(replayed):
    _, report = replayed
    outcome = {o.email_id: [r.code for r in o.results] for o in report.outcomes}
    assert outcome == {"DEMO-001": ["UPDATED"], "DEMO-002": ["UPDATED_NEEDS_REVIEW"], "DEMO-003": ["UPDATE_NOT_APPLIED"],
                       "DEMO-004": ["FOUND"], "DEMO-005": ["CANCELLED"], "DEMO-006": ["BOOKING_NOT_FOUND"]}


def test_the_demo_changes_exactly_what_it_says(replayed):
    engine, _ = replayed
    assert booking(engine, "BOOKINGREF-A400").requested_date == date(2026, 12, 1)           # applied, status unchanged
    assert booking(engine, "BOOKINGREF-A400").status == "READY" and booking(engine, "BOOKINGREF-A400").customer == "ABC Shipping"
    d200 = booking(engine, "BOOKINGREF-D200")
    assert (d200.quantity, d200.status) == (4, "VALIDATION_REQUIRED")                        # 4 units, 3 VINs: back to review
    assert booking(engine, "XYZ-REF-2026-1109").status == "CANCELLED"
    with Session(engine) as session:
        assert len(session.scalars(select(BookingRow)).all()) == 3                           # nothing was created
        events = [(e.source_email_id, e.event_type) for e in session.scalars(select(EventRow).order_by(EventRow.id))]
    assert events == [("DEMO-001", "UPDATE"), ("DEMO-002", "UPDATE"), ("DEMO-003", "FAILED_ACTION"),
                      ("DEMO-005", "CANCEL"), ("DEMO-006", "FAILED_ACTION")]
