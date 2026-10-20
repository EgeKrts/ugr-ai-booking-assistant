"""Opt-in regression checks against the real model: python -m pytest tests/test_live.py --live

Run these after changing the extraction prompt or the model. They check the outcomes that matter most
(intent, no invented values, injection ignored) on a handful of dataset e-mails, through extraction and validation.
"""
import pytest
from ugr_booking.config import get_dataset_path
from ugr_booking.extractor import Extractor
from ugr_booking.ingest import read_dataset_emails
from ugr_booking.master_data import MasterIndex, load_dataset
from ugr_booking.validators import validate_extraction

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def pipeline():
    path = get_dataset_path()
    if not path.exists():
        pytest.skip("dataset not available locally")
    emails = {e.email_id: e for e in read_dataset_emails(path).records}
    master = MasterIndex.from_dataset(load_dataset(path))
    extractor = Extractor()

    def run(email_id):
        email = emails[email_id]
        return validate_extraction(email, extractor.extract(email), master)
    return run


def draft(result, index=0):
    return result.bookings[index].draft


def codes(result):
    return {i.code for i in result.all_issues}


@pytest.mark.parametrize("email_id,intent", [
    ("EMAIL-002", "CREATE_BOOKING"), ("EMAIL-004", "NON_BOOKING"), ("EMAIL-006", "NON_BOOKING"), ("EMAIL-012", "CREATE_BOOKING"),
    ("EMAIL-013", "CANCEL_BOOKING"), ("EMAIL-015", "UPDATE_BOOKING"), ("EMAIL-024", "STATUS_INQUIRY"), ("EMAIL-025", "UNKNOWN"),
    ("EMAIL-028", "CREATE_BOOKING"), ("EMAIL-029", "CREATE_BOOKING"), ("EMAIL-030", "CANCEL_BOOKING")])
def test_intent(pipeline, email_id, intent):
    assert pipeline(email_id).intent.value == intent


def test_two_bookings_in_one_email(pipeline):
    assert len(pipeline("EMAIL-017").bookings) == 2


def test_the_latest_value_wins_inside_one_message(pipeline):
    assert draft(pipeline("EMAIL-028")).pod == "SAYNB"                 # corrected from Jeddah to Yanbu
    assert pipeline("EMAIL-015").bookings[0].draft.quantity == 4       # quoted history said 6


def test_the_quoted_create_does_not_create_anything(pipeline):
    result = pipeline("EMAIL-030")
    assert result.bookings == () and result.target_booking_ref == "BOOKINGREF-X300"


def test_an_embedded_instruction_does_not_change_the_port(pipeline):
    result = pipeline("EMAIL-031")
    assert draft(result).pod == "SAJED" and "PROMPT_INJECTION_SUSPECTED" in codes(result)


@pytest.mark.parametrize("email_id,code", [
    ("EMAIL-019", "VIN_COUNT_MISMATCH"), ("EMAIL-020", "INVALID_DATE"), ("EMAIL-021", "UNIT_UNRESOLVED"),
    ("EMAIL-032", "DIMENSION_FORMAT_MIXED"), ("EMAIL-001", "AMBIGUOUS_PORT"), ("EMAIL-010", "UNKNOWN_CUSTOMER")])
def test_problems_are_flagged_not_fixed(pipeline, email_id, code):
    result = pipeline(email_id)
    assert code in codes(result) and draft(result).processing_status.value == "VALIDATION_REQUIRED"


def test_nothing_is_invented_for_a_booking_with_details_in_an_attachment(pipeline):
    d = draft(pipeline("EMAIL-029"))
    assert (d.quantity, d.weight_kg_per_unit, d.vin_list, d.requested_date) == (None, None, [], None)
