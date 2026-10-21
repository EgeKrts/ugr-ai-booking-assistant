"""Business-rule tests with invented data; no model, no private dataset e-mails."""
from datetime import date, datetime
import pytest
from ugr_booking.master_data import MasterIndex
from ugr_booking.normalize import (
    fold, parse_dimensions, parse_localized_number, parse_requested_date, parse_weight,
)
from ugr_booking.schemas import (
    Customer, EmailRecord, ExtractedBooking, ExtractionResult, MessageIntent, Port,
    ProcessingStatus, Severity,
)
from ugr_booking.validators import validate_extraction

MASTER = MasterIndex(
    [Customer(customer_id="C1", name="Example Carrier", aliases=["Example Carrier Ltd.", "ExCar"]),
     Customer(customer_id="C2", name="Sample Freight", aliases=["Sample Frt"])],
    [Port(port_code="AAGEN", name="Genoa", aliases=["GENOA", "Genova"]),
     Port(port_code="BBJED", name="Jeddah", aliases=["JEDDAH", "JEDAH"]),
     Port(port_code="CCYAN", name="Yanbu", aliases=["YANBU"]),
     Port(port_code="DDIZM", name="Izmir", aliases=["IZMIR", "İZMİR"])],
)

BODY = """Customer: Example Carrier
POL: GENOA
POD: JEDDAH
Cargo: 2 x TRUCK
VIN: TESTVIN0001
VIN: TESTVIN0002
Weight each: 7.650 KG
Dims: 6,00 x 2,50 x 3,70 m
Requested date: 18-11-2026
"""

GOOD = dict(customer="Example Carrier", pol="GENOA", pod="JEDDAH", cargo_description="2 x TRUCK", quantity=2,
            vin_list=["TESTVIN0001", "TESTVIN0002"], weight_text="7.650 KG each",
            dimensions_text="6,00 x 2,50 x 3,70 m", requested_date_text="18-11-2026")


def email(body=BODY, suspicious="", quoted=""):
    return EmailRecord(email_id="EXAMPLE-001", received_at=datetime(2026, 10, 1, 9, 0), subject="Invented",
                       sender="x@example.test", raw_body=body, clean_body=body, quoted_body=quoted,
                       suspicious_text=suspicious, file_name="emails/EXAMPLE-001.txt")


def check(body=BODY, intent=MessageIntent.CREATE_BOOKING, target=None, suspicious="", quoted="", **fields):
    values = {**GOOD, **fields} if intent == MessageIntent.CREATE_BOOKING else fields
    extraction = ExtractionResult(intent=intent, bookings=[ExtractedBooking(**values)] if values else [],
                                  target_booking_ref=target)
    return validate_extraction(email(body, suspicious, quoted), extraction, MASTER)


def codes(result):
    return {issue.code for issue in result.all_issues}


def draft(result):
    return result.bookings[0].draft


# ------------------------------------------------------------ master data

def test_known_customer_alias():
    result = check(body=BODY.replace("Example Carrier", "ExCar ltd"), customer="ExCar ltd")
    assert result.bookings[0].draft.customer is None  # "ExCar ltd" is not an alias
    result = check(body=BODY.replace("Example Carrier", "example carrier ltd"), customer="example carrier ltd")
    assert draft(result).customer == "Example Carrier"


def test_known_port_alias():
    body = BODY.replace("GENOA", "Genova").replace("JEDDAH", "JEDAH")
    result = check(body=body, pol="Genova", pod="JEDAH")
    assert (draft(result).pol, draft(result).pod) == ("AAGEN", "BBJED")


def test_turkish_dotted_capital_i_matches():
    assert MASTER.match_port("İZMİR").value == "DDIZM"
    assert MASTER.match_port("izmir").value == "DDIZM"
    assert fold("İZMİR") == "izmir"


def test_unknown_customer_requires_review():
    result = check(body=BODY.replace("Example Carrier", "Nobody Ltd"), customer="Nobody Ltd")
    assert "UNKNOWN_CUSTOMER" in codes(result)
    assert draft(result).customer is None and draft(result).processing_status == ProcessingStatus.VALIDATION_REQUIRED


def test_garbled_port_is_not_guessed():
    body = BODY.replace("JEDDAH", "JED�AH")
    result = check(body=body, pod="JED�AH")
    assert "UNKNOWN_PORT" in codes(result) and draft(result).pod is None


def test_two_ports_in_one_field_is_ambiguous():
    result = check(body=BODY.replace("JEDDAH", "JEDDAH / YANBU"), pod="JEDDAH / YANBU")
    assert "AMBIGUOUS_PORT" in codes(result) and draft(result).pod is None


def test_same_loading_and_discharge_port_needs_review():
    assert "SAME_POL_POD" in codes(check(body=BODY.replace("JEDDAH", "GENOA"), pod="GENOA"))


# ---------------------------------------------------------- READY and gaps

def test_complete_booking_is_ready_and_normalised():
    result = check()
    assert codes(result) == set()
    d = draft(result)
    assert d.processing_status == ProcessingStatus.READY
    assert (d.weight_kg_per_unit, d.length_m, d.width_m, d.height_m) == (7650.0, 6.0, 2.5, 3.7)
    assert d.requested_date == date(2026, 11, 18)


def test_ready_is_never_assigned():
    assert draft(check()).processing_status != ProcessingStatus.ASSIGNED


def test_missing_critical_fields_require_review():
    result = check(body="nothing useful here", customer=None, pol=None, pod=None, cargo_description=None,
                   quantity=None, vin_list=[], weight_text=None, dimensions_text=None, requested_date_text=None)
    assert {"MISSING_FIELD"} <= codes(result)
    missing = {i.field for i in result.all_issues if i.code == "MISSING_FIELD"}
    assert {"customer", "pol", "pod", "cargo_description", "quantity", "requested_date"} <= missing
    assert draft(result).processing_status == ProcessingStatus.VALIDATION_REQUIRED


def test_optional_contact_and_vessel_do_not_block_ready():
    d = draft(check())
    assert d.vessel is None and d.contact_email is None and d.processing_status == ProcessingStatus.READY


def test_missing_weight_is_only_a_warning():
    result = check(weight_text=None)
    assert draft(result).processing_status == ProcessingStatus.READY
    assert {i.severity for i in result.all_issues if i.code == "WEIGHT_MISSING"} == {Severity.WARNING}


@pytest.mark.parametrize("quantity", [0, -3])
def test_quantity_must_be_positive(quantity):
    assert "INVALID_QUANTITY" in codes(check(quantity=quantity))


# -------------------------------------------------------------------- VINs

def test_quantity_vin_mismatch_requires_review():
    result = check(body=BODY.replace("2 x TRUCK", "3 x TRUCK"), cargo_description="3 x TRUCK", quantity=3)
    assert "VIN_COUNT_MISMATCH" in codes(result)
    assert draft(result).processing_status == ProcessingStatus.VALIDATION_REQUIRED
    assert draft(result).quantity == 3  # the stated quantity is kept, not "fixed" from the VIN count


def test_duplicate_vin_is_flagged():
    body = BODY.replace("TESTVIN0002", "TESTVIN0001")
    assert "DUPLICATE_VIN" in codes(check(body=body, vin_list=["TESTVIN0001", "TESTVIN0001"]))


def test_vin_not_in_email_is_rejected():
    result = check(vin_list=["TESTVIN0001", "MADEUP99999"])
    assert "VIN_NOT_IN_SOURCE" in codes(result) and "MADEUP99999" not in draft(result).vin_list


def test_vin_with_odd_format_is_flagged():
    body = BODY + "VIN: TESTVIN000X\nVIN: TESTVIN0003\n"
    vins = ["TESTVIN0001", "TESTVIN0002", "TESTVIN0003", "TESTVIN000X"]
    assert "VIN_FORMAT_UNUSUAL" in codes(check(body=body, quantity=4, vin_list=vins))


# ------------------------------------------------------------------- dates

def test_invalid_date_not_autocorrected():
    result = check(body=BODY.replace("18-11-2026", "31/02/2026"), requested_date_text="31/02/2026")
    assert "INVALID_DATE" in codes(result) and draft(result).requested_date is None


@pytest.mark.parametrize("text", ["SEPTEMBER SAILING", "next week", "Same sailing week if possible"])
def test_ambiguous_date_not_fabricated(text):
    result = check(body=BODY.replace("18-11-2026", text), requested_date_text=text)
    assert "AMBIGUOUS_DATE" in codes(result) and draft(result).requested_date is None


def test_earliest_date_is_not_a_sailing_date():
    text = "next available after 25/11/2026"
    result = check(body=BODY.replace("18-11-2026", text), requested_date_text=text)
    assert "DATE_NOT_EXACT" in codes(result) and draft(result).requested_date is None
    assert "2026-11-25" in next(i.message for i in result.all_issues if i.code == "DATE_NOT_EXACT")


def test_date_before_the_email_was_received_is_warned_not_blocked():
    result = check(body=BODY.replace("18-11-2026", "01-09-2026"), requested_date_text="01-09-2026")
    assert "DATE_IN_PAST" in codes(result) and draft(result).processing_status == ProcessingStatus.READY


def test_parse_requested_date_formats():
    assert parse_requested_date("27/11/26").value == date(2026, 11, 27)
    assert parse_requested_date("20 October 2026").value == date(2026, 10, 20)
    assert parse_requested_date("2026-11-05").value == date(2026, 11, 5)
    assert parse_requested_date("null").kind == "missing" and parse_requested_date(None).kind == "missing"


# ------------------------------------------------------- weight and dimensions

def test_missing_weight_unit_not_assumed():
    result = check(body=BODY.replace("7.650 KG", "7200"), weight_text="7200")
    assert "UNIT_UNRESOLVED" in codes(result) and draft(result).weight_kg_per_unit is None
    assert draft(result).processing_status == ProcessingStatus.VALIDATION_REQUIRED


@pytest.mark.parametrize("text,kg", [
    ("12.500,50 KG", 12500.5), ("7.330,50 KG", 7330.5), ("7,330 KG", 7330.0), ("7.650 KG", 7650.0),
    ("7 450 KG", 7450.0), ("12,400 KG", 12400.0), ("8000 KGS", 8000.0), ("7,5 KG", 7.5),
])
def test_european_decimal_normalization(text, kg):
    assert parse_weight(text).kg == kg


def test_weight_without_each_for_several_units_is_not_stored_per_unit():
    body = BODY.replace("Weight each: 7.650 KG", "Weight: 7650 KG")
    result = check(body=body, weight_text="7650 KG")
    assert "WEIGHT_BASIS_UNCLEAR" in codes(result) and draft(result).weight_kg_per_unit is None


def test_each_in_the_source_label_counts_even_if_the_model_moves_it():
    assert draft(check(weight_text="7.650 KG")).weight_kg_per_unit == 7650.0


def test_unsupported_weight_unit_is_not_converted():
    assert parse_weight("5 tons").code == "UNIT_UNSUPPORTED"


def test_weight_number_not_in_email_is_rejected():
    assert "VALUE_NOT_IN_SOURCE" in codes(check(weight_text="9999 KG each"))


def test_centimetres_become_metres():
    d = parse_dimensions("650 x 227 x 260 cm")
    assert (d.length_m, d.width_m, d.height_m) == (6.5, 2.27, 2.6)


def test_mixed_decimal_marks_are_not_normalised():
    result = parse_dimensions("6,20 x 2.450 x 3,10 m")
    assert result.code == "DIMENSION_FORMAT_MIXED" and result.length_m is None


def test_dimensions_without_unit_or_out_of_range():
    assert parse_dimensions("6 x 2.5 x 4").code == "UNIT_UNRESOLVED"
    assert parse_dimensions("2450 x 2 x 3 m").code == "DIMENSION_OUT_OF_RANGE"


def test_labelled_dimensions_without_unit_are_unresolved():
    assert parse_dimensions("L 6.2  W 2.5  H 3.9").code == "UNIT_UNRESOLVED"
    assert parse_dimensions("L 6.2 W 2.5 H 3.9 m").length_m == 6.2


def test_localized_number_rejects_nonsense():
    assert parse_localized_number("1.2.3") is None and parse_localized_number("abc") is None


# ----------------------------------------------------------- update / cancel

def test_update_without_reference_does_not_create_a_booking():
    result = check(body="Please change POD to YANBU", intent=MessageIntent.UPDATE_BOOKING, pod="YANBU")
    assert result.action_unresolved and result.target_booking_ref is None
    assert "MISSING_REFERENCE" in codes(result)
    assert all(b.draft.processing_status != ProcessingStatus.READY for b in result.bookings)


def test_cancel_with_reference_present_in_the_email():
    result = check(body="Please CANCEL REF-77.", intent=MessageIntent.CANCEL_BOOKING, target="REF-77")
    assert result.target_booking_ref == "REF-77" and not result.action_unresolved and result.bookings == ()


def test_reference_not_in_email_is_not_trusted():
    result = check(body="Please CANCEL it.", intent=MessageIntent.CANCEL_BOOKING, target="REF-INVENTED")
    assert result.action_unresolved and "REFERENCE_NOT_IN_SOURCE" in codes(result)


def test_update_keeps_only_the_changed_fields():
    result = check(body="Update REF-5: quantity 6", intent=MessageIntent.UPDATE_BOOKING, target="REF-5", quantity=6)
    assert draft(result).quantity == 6 and draft(result).customer is None
    assert draft(result).processing_status == ProcessingStatus.PARSED
    assert "MISSING_FIELD" not in codes(result)


@pytest.mark.parametrize("intent", [MessageIntent.STATUS_INQUIRY, MessageIntent.NON_BOOKING, MessageIntent.UNKNOWN])
def test_non_creating_intents_never_produce_bookings(intent):
    result = validate_extraction(
        email("Where is REF-9?"), ExtractionResult(intent=intent, target_booking_ref="REF-9",
                                                     bookings=[ExtractedBooking(**GOOD)]), MASTER)
    assert result.bookings == () and "BOOKINGS_IGNORED" in codes(result)


def test_status_inquiry_keeps_a_grounded_reference():
    result = validate_extraction(email("Where is REF-9?"),
                                 ExtractionResult(intent=MessageIntent.STATUS_INQUIRY, target_booking_ref="REF-9"), MASTER)
    assert result.target_booking_ref == "REF-9"


def test_create_with_no_extracted_booking_is_an_issue():
    result = validate_extraction(email(), ExtractionResult(intent=MessageIntent.CREATE_BOOKING), MASTER)
    assert "NO_BOOKING_EXTRACTED" in codes(result) and result.bookings == ()


def test_two_bookings_are_validated_separately():
    extraction = ExtractionResult(intent=MessageIntent.CREATE_BOOKING,
                                  bookings=[ExtractedBooking(**GOOD), ExtractedBooking(**{**GOOD, "pod": "YANBU"})])
    result = validate_extraction(email(BODY + "POD YANBU"), extraction, MASTER)
    assert [b.booking_index for b in result.bookings] == [0, 1]
    assert [b.draft.pod for b in result.bookings] == ["BBJED", "CCYAN"]


def test_voyage_without_a_number_is_not_stored():
    body = BODY + "ex GENOA\n"
    result = check(body=body, voyage="ex GENOA")
    assert draft(result).voyage is None and "VOYAGE_UNRECOGNISED" in codes(result)


# -------------------------------------------------------- source-evidence regressions

def test_unstated_model_fields_cannot_become_ready():
    body = "POL: GENOA\nPOD: JEDDAH\nRequested date: 18-11-2026\n"
    result = check(body=body, customer="Sample Freight", cargo_description="HELICOPTER", quantity=999,
                   vin_list=[], weight_text=None, dimensions_text=None, vessel="Invented Vessel",
                   voyage="999Z", rate_text="EUR 9999", contact_name="Invented Person",
                   contact_email="madeup@example.test")
    d = draft(result)
    assert d.processing_status == ProcessingStatus.VALIDATION_REQUIRED
    assert (d.customer, d.cargo_description, d.quantity, d.vessel, d.voyage, d.rate_text,
            d.contact_name, d.contact_email) == (None,) * 8
    assert "VALUE_NOT_IN_SOURCE" in codes(result)


def test_customer_canonical_name_can_be_supported_by_a_source_alias():
    result = check(body=BODY.replace("Example Carrier", "ExCar"))
    assert draft(result).customer == "Example Carrier"
    assert draft(result).processing_status == ProcessingStatus.READY


@pytest.mark.parametrize("quantity", [999, 2**63])
def test_quantity_needs_source_evidence_and_a_storable_range(quantity):
    result = check(body="Booking ref REF-999\nPOL: GENOA\nPOD: JEDDAH\nDate: 18-11-2026",
                   quantity=quantity, vin_list=[])
    assert draft(result).quantity is None
    assert draft(result).processing_status == ProcessingStatus.VALIDATION_REQUIRED


def test_model_cannot_change_the_source_weight_unit():
    result = check(body=BODY.replace("7.650 KG", "7.650 lbs"))
    assert draft(result).weight_kg_per_unit is None
    assert draft(result).processing_status == ProcessingStatus.VALIDATION_REQUIRED
    assert "UNIT_UNSUPPORTED" in codes(result)


def test_model_cannot_invent_a_per_unit_basis():
    result = check(body=BODY.replace("Weight each:", "Weight:"))
    assert draft(result).weight_kg_per_unit is None
    assert "WEIGHT_BASIS_UNCLEAR" in codes(result)


def test_model_cannot_change_the_source_dimension_unit():
    body = BODY.replace("6,00 x 2,50 x 3,70 m", "6,00 x 2,50 x 3,70 cm")
    result = check(body=body)
    assert draft(result).length_m is None
    assert draft(result).processing_status == ProcessingStatus.VALIDATION_REQUIRED
    assert "VALUE_NOT_IN_SOURCE" in codes(result)


@pytest.mark.parametrize("alternatives", ["JEDDAH / YANBU", "JEDDAH or YANBU"])
def test_model_cannot_silently_choose_one_of_the_source_ports(alternatives):
    result = check(body=BODY.replace("POD: JEDDAH", "POD: " + alternatives), pod="JEDDAH")
    assert draft(result).pod is None
    assert "AMBIGUOUS_PORT" in codes(result)
    assert draft(result).processing_status == ProcessingStatus.VALIDATION_REQUIRED


def test_separate_loading_and_discharge_ports_are_not_ambiguous():
    result = check(body=BODY.replace("POL: GENOA\nPOD: JEDDAH", "POL: GENOA / POD: JEDDAH"))
    assert (draft(result).pol, draft(result).pod) == ("AAGEN", "BBJED")
    assert draft(result).processing_status == ProcessingStatus.READY


def test_explicit_unresolved_customer_blocks_ready_even_with_a_value():
    result = validate_extraction(
        email(), ExtractionResult(intent=MessageIntent.CREATE_BOOKING,
                                  bookings=[ExtractedBooking(**GOOD)],
                                  unresolved_fields=["customer"], message_notes="Customer is uncertain"),
        MASTER)
    assert "MODEL_UNRESOLVED" in codes(result)
    assert draft(result).processing_status == ProcessingStatus.VALIDATION_REQUIRED


def test_non_finite_localized_number_is_rejected():
    assert parse_localized_number("9" * 400) is None
    assert parse_weight("9" * 400 + " KG each").code == "WEIGHT_UNPARSEABLE"


def test_a_negative_source_count_cannot_support_a_positive_model_quantity():
    result = check(body=BODY.replace("2 x TRUCK", "-2 x TRUCK"))
    assert draft(result).quantity is None
    assert "VALUE_NOT_IN_SOURCE" in codes(result)


def test_an_unresolved_optional_absent_contact_does_not_block_ready():
    result = validate_extraction(
        email(), ExtractionResult(intent=MessageIntent.CREATE_BOOKING,
                                  bookings=[ExtractedBooking(**GOOD)],
                                  unresolved_fields=["contact_name"]),
        MASTER)
    assert draft(result).processing_status == ProcessingStatus.READY
    issue = next(i for i in result.all_issues if i.code == "MODEL_UNRESOLVED")
    assert issue.severity == Severity.WARNING
