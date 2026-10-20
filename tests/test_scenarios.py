"""The 34 dataset e-mails through the whole pipeline (validation, tools, database), model output replayed.

These tests need the dataset (data/private/dataset) and a recording made with
`python -m ugr_booking.cli record-extractions` (data/private/extractions). Both stay out of Git, so on a
machine without them this module is skipped. Expectations are hypotheses derived from reading the e-mails
(see the plan's scenario matrix), not an official answer key. No e-mail text is stored in this file.
"""
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from ugr_booking.config import get_dataset_path
from ugr_booking.db import BookingRow, EmailRow, EventRow, IssueRow, make_engine
from ugr_booking.ingest import read_dataset_emails
from ugr_booking.master_data import MasterIndex, load_dataset
from ugr_booking.orchestrator import DirectPlanner, Orchestrator
from ugr_booking.recorded import RecordedExtractor

RECORDING = Path("data/private/extractions")
DATASET = get_dataset_path()
if not DATASET.exists() or not (RECORDING / "EMAIL-001.json").exists():
    pytest.skip("dataset or recorded extractions not available locally", allow_module_level=True)


@dataclass
class Facts:
    email: EmailRow
    bookings: list[BookingRow]
    events: list[EventRow]
    issues: list[IssueRow]

    @property
    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    @property
    def event_types(self) -> list[str]:
        return [e.event_type for e in self.events]

    @property
    def extraction(self) -> dict:
        return self.email.extraction or {}

    def issue_fields(self, code: str) -> set[str | None]:
        return {i.field for i in self.issues if i.code == code}

    def only(self) -> BookingRow:
        assert len(self.bookings) == 1, f"expected one booking, found {len(self.bookings)}"
        return self.bookings[0]


@pytest.fixture(scope="module")
def world():
    dataset = load_dataset(DATASET)
    master = MasterIndex.from_dataset(dataset)
    emails = list(read_dataset_emails(DATASET).records)
    engine = make_engine("sqlite://")
    extractor = RecordedExtractor(RECORDING)
    orchestrator = Orchestrator(engine, extractor, master, DirectPlanner())
    report = orchestrator.ingest(emails)
    with Session(engine) as session:
        facts = {}
        for email in session.scalars(select(EmailRow)):
            facts[email.email_id] = Facts(
                email,
                list(session.scalars(select(BookingRow).where(BookingRow.source_email_id == email.email_id).order_by(BookingRow.id))),
                list(session.scalars(select(EventRow).where(EventRow.source_email_id == email.email_id).order_by(EventRow.id))),
                list(session.scalars(select(IssueRow).where(IssueRow.email_id == email.email_id).order_by(IssueRow.id))))
        session.expunge_all()
    return {"engine": engine, "facts": facts, "report": report, "orchestrator": orchestrator, "emails": emails,
            "master": master, "extractor": extractor}


# --------------------------------------------------------------------- expectations

def review(*codes, **fields):
    return dict(status="VALIDATION_REQUIRED", codes=set(codes), **fields)


def ready(**fields):
    return dict(status="READY", **fields)


def not_applied(intent):
    return dict(intent=intent, bookings=0, events=["FAILED_ACTION"])


NO_BOOKING = dict(bookings=0, events=[])

EXPECTED = {
    "EMAIL-001": dict(intent="CREATE_BOOKING", **review("AMBIGUOUS_PORT", "AMBIGUOUS_DATE"),
                      check=lambda b: b.pod is None and b.requested_date is None and b.pol == "ITGOA"),
    "EMAIL-002": dict(intent="CREATE_BOOKING", **ready(check=lambda b: (b.pol, b.pod, b.quantity, b.weight_kg_per_unit, b.requested_date)
                                                       == ("ESSAG", "SAJED", 3, 8000.0, date(2026, 10, 10)))),
    "EMAIL-003": dict(**not_applied("UPDATE_BOOKING"), codes={"VIN_COUNT_MISMATCH"}),
    "EMAIL-004": dict(intent="NON_BOOKING", **NO_BOOKING),
    "EMAIL-005": dict(intent="CREATE_BOOKING", **review("MISSING_FIELD"),
                      check=lambda b: b.pol is None and b.pod is None and b.requested_date is None and b.dimensions_m["length"] == 6.5),
    "EMAIL-006": dict(intent="NON_BOOKING", **NO_BOOKING),
    "EMAIL-007": not_applied("UPDATE_BOOKING"),
    "EMAIL-008": dict(intent="CREATE_BOOKING", events=["CREATE"], status="CANCELLED",
                      check=lambda b: b.external_ref == "BOOKINGREF-D100" and b.quantity == 5),   # cancelled later by EMAIL-013
    "EMAIL-009": dict(intent="CREATE_BOOKING", **ready(check=lambda b: (b.weight_kg_per_unit, b.dimensions_m)
                                                       == (12500.5, {"length": 12.1, "width": 2.55, "height": 3.8}))),
    "EMAIL-010": dict(intent="CREATE_BOOKING", **review("UNKNOWN_CUSTOMER", check=lambda b: b.customer is None)),
    "EMAIL-011": dict(intent="CREATE_BOOKING", **review("VIN_FORMAT_UNUSUAL")),
    "EMAIL-012": dict(intent="CREATE_BOOKING", bookings=0, events=["DUPLICATE"]),
    "EMAIL-013": dict(intent="CANCEL_BOOKING", bookings=0, events=["CANCEL"]),
    "EMAIL-014": not_applied("UPDATE_BOOKING"),
    "EMAIL-015": dict(**not_applied("UPDATE_BOOKING"), extracted_quantity=4),     # the latest instruction, not the quoted 6
    "EMAIL-016": dict(intent="CREATE_BOOKING", **ready(check=lambda b: b.pol == "GRPIR" and b.pod == "SAYNB")),
    "EMAIL-017": dict(intent="CREATE_BOOKING", bookings=2, events=["CREATE", "CREATE"],
                      check_all=lambda bs: {b.pod for b in bs} == {"SAJED", "SAYNB"} and {b.quantity for b in bs} == {1, 2}),
    "EMAIL-018": dict(intent="CREATE_BOOKING", **review("MISSING_FIELD", "AMBIGUOUS_DATE",
                      check=lambda b: b.pol is None and b.pod is None and b.requested_date is None)),
    "EMAIL-019": dict(intent="CREATE_BOOKING", **review("VIN_COUNT_MISMATCH", check=lambda b: b.quantity == 4 and len(b.vin_list) == 5)),
    "EMAIL-020": dict(intent="CREATE_BOOKING", **review("INVALID_DATE", check=lambda b: b.requested_date is None)),
    "EMAIL-021": dict(intent="CREATE_BOOKING", **review("UNIT_UNRESOLVED", check=lambda b: b.weight_kg_per_unit is None)),
    "EMAIL-022": dict(intent="CREATE_BOOKING", **ready(check=lambda b: b.pod == "SAJED")),       # JEDAH alias
    "EMAIL-023": dict(**not_applied("UPDATE_BOOKING"), extracted_quantity=6),
    "EMAIL-024": dict(intent="STATUS_INQUIRY", **NO_BOOKING),
    "EMAIL-025": dict(intent="UNKNOWN", **NO_BOOKING, warnings={"EMPTY_BODY"}),
    "EMAIL-026": dict(intent="CREATE_BOOKING", **review("UNKNOWN_PORT", check=lambda b: b.pod is None), warnings={"MISSING_SENDER", "SUSPECT_ENCODING"}),
    "EMAIL-027": dict(intent="CREATE_BOOKING", **ready(check=lambda b: b.quantity == 2)),
    "EMAIL-028": dict(intent="CREATE_BOOKING", **ready(check=lambda b: b.pod == "SAYNB" and b.external_ref == "BOOKINGREF-D200")),   # the correction wins
    "EMAIL-029": dict(intent="CREATE_BOOKING", **review("MISSING_FIELD", check=lambda b: (b.customer, b.pol, b.pod, b.quantity, b.weight_kg_per_unit, b.vin_list)
                                                       == ("ABC Shipping", "ITGOA", "SAJED", None, None, []))),    # nothing invented
    "EMAIL-030": dict(**not_applied("CANCEL_BOOKING"), no_extracted_bookings=True),             # the quoted CREATE is ignored
    "EMAIL-031": dict(intent="CREATE_BOOKING", **review("PROMPT_INJECTION_SUSPECTED", check=lambda b: b.pod == "SAJED" and b.raw["pod"] != "DUBAI")),
    "EMAIL-032": dict(intent="CREATE_BOOKING", **review("DIMENSION_FORMAT_MIXED", "DATE_NOT_EXACT",
                      check=lambda b: b.dimensions_m is None and b.requested_date is None)),
    "EMAIL-033": dict(intent="CREATE_BOOKING", **ready(check=lambda b: (b.pol, b.pod) == ("TRIZM", "ITLIV"))),
    "EMAIL-034": dict(intent="CREATE_BOOKING", **ready(check=lambda b: (b.pol, b.weight_kg_per_unit, b.quantity) == ("TRIZM", 7650.0, 2))),
}


def test_every_dataset_email_has_an_expectation(world):
    assert set(EXPECTED) == set(world["facts"]) and len(EXPECTED) == 34


@pytest.mark.parametrize("email_id", sorted(EXPECTED))
def test_scenario(world, email_id):
    expect, facts = EXPECTED[email_id], world["facts"][email_id]
    assert facts.email.status in ("PROCESSED", "REVIEW_REQUIRED"), facts.email.error      # never failed, never skipped
    if "intent" in expect:
        assert facts.email.intent == expect["intent"]
    if "bookings" in expect:
        assert len(facts.bookings) == expect["bookings"]
    if "events" in expect:
        assert facts.event_types == expect["events"]
    if "codes" in expect:
        assert expect["codes"] <= facts.codes, f"missing {expect['codes'] - facts.codes}"
    if "warnings" in expect:
        assert expect["warnings"] <= set(facts.email.warnings)
    if "status" in expect:
        assert all(b.status == expect["status"] for b in facts.bookings) and facts.bookings
    if "check" in expect:
        assert expect["check"](facts.only()), f"{email_id} booking: {facts.only().__dict__}"
    if "check_all" in expect:
        assert expect["check_all"](facts.bookings)
    if "extracted_quantity" in expect:
        assert facts.extraction["bookings"][0]["quantity"] == expect["extracted_quantity"]
    if expect.get("no_extracted_bookings"):
        assert facts.extraction["bookings"] == []


# ------------------------------------------------------------------ batch invariants

def test_all_34_emails_were_processed_and_none_failed(world):
    summary = world["report"].summary()
    assert summary["email_total"] == 34 and summary["processed"] == 34 and summary["failed"] == 0
    assert summary["skipped_already_processed"] == 0


def test_batch_numbers_match_the_reference_run(world):
    summary = world["report"].summary()
    assert (summary["created"], summary["cancelled"], summary["duplicated"], summary["failed_actions"], summary["conflicts"]) == (23, 1, 1, 6, 0)
    assert (summary["ready"], summary["validation_required"], summary["updated"]) == (9, 14, 0)


def test_only_booking_requests_create_bookings(world):
    for email_id, facts in world["facts"].items():
        if facts.email.intent in ("NON_BOOKING", "STATUS_INQUIRY", "UNKNOWN", "UPDATE_BOOKING", "CANCEL_BOOKING"):
            assert facts.bookings == [] and "CREATE" not in facts.event_types, email_id


def test_no_ready_booking_has_an_open_error(world):
    with Session(world["engine"]) as session:
        for booking in session.scalars(select(BookingRow).where(BookingRow.status == "READY")):
            errors = session.scalars(select(IssueRow).where(IssueRow.severity == "ERROR").where(
                (IssueRow.booking_id == booking.id) | ((IssueRow.booking_id.is_(None)) & (IssueRow.email_id == booking.source_email_id)))).all()
            assert errors == [], booking.display_id


def test_stored_values_are_master_data_values_or_missing(world):
    ports = set(world["master"].port_names)
    customers = {name for name, _, _ in world["master"].find_in_text(" ".join(world["master"]._customers))[0]} or None
    with Session(world["engine"]) as session:
        for booking in session.scalars(select(BookingRow)):
            assert booking.pol in ports | {None} and booking.pod in ports | {None}, booking.display_id
            assert booking.status != "ASSIGNED"                       # no sailing is ever assigned


def test_every_booking_and_event_traces_back_to_a_source_email(world):
    with Session(world["engine"]) as session:
        emails = set(session.scalars(select(EmailRow.email_id)))
        assert {b.source_email_id for b in session.scalars(select(BookingRow))} <= emails
        assert {e.source_email_id for e in session.scalars(select(EventRow))} <= emails


def test_running_the_batch_again_changes_nothing(world):
    engine = world["engine"]

    def counts():
        with Session(engine) as session:
            return [len(session.scalars(select(model)).all()) for model in (BookingRow, EventRow, IssueRow, EmailRow)]

    before = counts()
    again = world["orchestrator"].ingest(world["emails"])
    assert counts() == before and again.summary()["skipped_already_processed"] == 34
