"""Deterministic business validation of an unvalidated ExtractionResult.

The model's text is a claim. A value reaches `BookingDraft` only when it occurs
in the e-mail, matches master data or parses without guessing. Everything else
becomes a ValidationIssue and the booking stays VALIDATION_REQUIRED.
"""
from __future__ import annotations
import re
from datetime import date
from .master_data import MasterIndex
from .normalize import (dimension_expressions, number_tokens, parse_dimensions, parse_requested_date,
                        parse_weight, squash, weight_expressions)
from .schemas import (
    BookingDraft, EmailRecord, ExtractedBooking, ExtractionResult, MessageIntent,
    ProcessingStatus, Severity, ValidatedBooking, ValidationIssue, ValidationResult,
)

# Fields a CREATE needs before it can be READY. Contact and vessel/voyage stay optional.
CRITICAL_FIELDS = ("customer", "pol", "pod", "cargo_description", "quantity", "requested_date")

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class _Issues:
    def __init__(self, index: int | None):
        self.index = index
        self.items: list[ValidationIssue] = []

    def add(self, code: str, field: str | None, message: str, severity: Severity = Severity.ERROR) -> None:
        self.items.append(ValidationIssue(
            code=code, field=field, message=message, severity=severity, booking_index=self.index))


def _in_source(value: str, source: str) -> bool:
    value = squash(value)
    return bool(value) and re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", source) is not None


def _numbers_in_source(text: str, source: str) -> bool:
    """The model may reorder words ("900 KG each" for "Weight each: 900 KG"),
    but every number it reports must occur in the e-mail exactly as written."""
    numbers = number_tokens(text)
    return bool(numbers) and all(n in number_tokens(source) for n in numbers)



_QUANTITY_CUE = r"(?:units?|vehicles?|trucks?|cars?|vans?|trailers?|buses|coaches|pieces?|pcs|excavators?|tractors?|machines?|chassis|heads?|SUVs?)"
_FIELD_LABEL = re.compile(
    r"\b(POL|POD|port\s+of\s+loading|port\s+of\s+discharge|customer|cargo|quantity|qty|weight|dims?|dimensions?|requested\s+date)\s*[:=]?",
    re.IGNORECASE)
_SMALL_COUNTS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _quantity_in_source(quantity: int, lines: list[str]) -> bool:
    """Require an explicit unit count, not a digit in a VIN, date or reference."""
    tokens = [str(quantity)]
    if quantity in _SMALL_COUNTS:
        tokens.append(_SMALL_COUNTS[quantity])
    value = r"(?<![\w.,+-])(?:" + "|".join(tokens) + r")(?![\w.,])"
    patterns = (
        r"\b(?:qty|quantity|unit\s+count|number\s+of\s+(?:units|vehicles))\s*[:=]?\s*" + value,
        value + r"\s*(?:x\s*)?(?:(?:used|new|head|heavy|passenger|commercial|pickup|pick-up)\s+)*" + _QUANTITY_CUE + r"\b",
        value + r"\s*[x\u00d7]\s+[A-Za-z]",
    )
    return any(re.search(pattern, line, re.IGNORECASE) for line in lines for pattern in patterns)


def _source_port_is_ambiguous(field: str, lines: list[str], master: MasterIndex) -> bool:
    labels = {"pol": {"pol", "port of loading"}, "pod": {"pod", "port of discharge"}}
    for line in lines:
        matches = list(_FIELD_LABEL.finditer(line))
        for index, label in enumerate(matches):
            if squash(label.group(1)) not in labels[field]:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            clause = line[label.end():end].split("|", 1)[0]
            ports = {code for code, _, _ in master.find_in_text(clause)[1]}
            if len(ports) > 1 and re.search(r"/|\bor\b|\bveya\b", clause, re.IGNORECASE):
                return True
    return False


def _source_weight(text: str, lines: list[str]):
    """Resolve the model's number against original number/unit/basis evidence."""
    numbers = number_tokens(text)
    evidence = [parse_weight(phrase) for line in lines for phrase in weight_expressions(line)
                if number_tokens(phrase) == numbers]
    valid = [item for item in evidence if not item.code]
    if valid:
        # Conflicting source statements are not resolved by picking the model's favourite.
        if len({(item.kg, item.per_unit) for item in valid}) > 1:
            return None
        return valid[0]
    return evidence[0] if evidence else None


def _vin_shape(vin: str) -> str:
    return "".join("A" if ch.isalpha() else "9" for ch in vin)


def _validate_booking(raw: ExtractedBooking, index: int, email: EmailRecord, source: str, lines: list[str],
                      master: MasterIndex, intent: MessageIntent, email_blocked: bool,
                      unresolved_fields: tuple[str, ...] = ()) -> ValidatedBooking:
    partial = intent != MessageIntent.CREATE_BOOKING
    issues = _Issues(index)
    out: dict = {}
    aliases = {"requested_date_text": "requested_date", "weight_text": "weight_kg_per_unit",
               "dimensions_text": "dimensions", "cargo": "cargo_description"}
    for field in unresolved_fields:
        name = aliases.get(field.strip().casefold(), field.strip().casefold())
        if name in CRITICAL_FIELDS or hasattr(raw, field):
            supplied = getattr(raw, field, None)
            severity = Severity.ERROR if name in CRITICAL_FIELDS or supplied or partial else Severity.WARNING
            issues.add("MODEL_UNRESOLVED", name, f"The model explicitly marked {name} as unresolved", severity)

    def missing(field: str, label: str | None = None) -> None:
        if not partial:
            issues.add("MISSING_FIELD", field, f"{label or field} is not stated in the e-mail")

    # --- reference -------------------------------------------------------
    if raw.booking_ref:
        if _in_source(raw.booking_ref, source):
            out["booking_ref"] = raw.booking_ref.strip()
        else:
            issues.add("REFERENCE_NOT_IN_SOURCE", "booking_ref", "Booking reference does not occur in the e-mail")

    # --- customer --------------------------------------------------------
    if raw.customer:
        match = master.match_customer(raw.customer)
        if match.status == "matched":
            customer_source = " ".join([email.subject, email.clean_body, email.quoted_body, email.sender or ""])
            supported = {name for name, _, _ in master.find_in_text(customer_source)[0]}
            if match.value in supported:
                out["customer"] = match.value
            else:
                issues.add("VALUE_NOT_IN_SOURCE", "customer", "Customer is known but is not stated in this e-mail")
        elif match.status == "ambiguous":
            issues.add("AMBIGUOUS_CUSTOMER", "customer", f"Customer matches several master records: {', '.join(match.candidates)}")
        else:
            issues.add("UNKNOWN_CUSTOMER", "customer", "Customer is not in the customer master data")
    else:
        missing("customer")

    # --- ports -----------------------------------------------------------
    for field in ("pol", "pod"):
        text = getattr(raw, field)
        if not text:
            missing(field, "Port of loading" if field == "pol" else "Port of discharge")
        elif _source_port_is_ambiguous(field, lines, master):
            issues.add("AMBIGUOUS_PORT", field, f"The source {field.upper()} clause names alternative ports")
        elif not _in_source(text, source):
            issues.add("VALUE_NOT_IN_SOURCE", field, f"{field.upper()} value does not occur in the e-mail text")
        else:
            match = master.match_port(text)
            if match.status == "matched":
                out[field] = match.value
            elif match.status == "ambiguous":
                issues.add("AMBIGUOUS_PORT", field, f"Several ports are named: {', '.join(match.candidates)}")
            else:
                issues.add("UNKNOWN_PORT", field, f"{field.upper()} cannot be matched to a master port")
    if out.get("pol") and out.get("pol") == out.get("pod"):
        issues.add("SAME_POL_POD", "pod", "Port of loading and discharge are the same port")

    # --- cargo and quantity ----------------------------------------------
    if raw.cargo_description and raw.cargo_description.strip():
        if _in_source(raw.cargo_description, source):
            out["cargo_description"] = raw.cargo_description.strip()
        else:
            issues.add("VALUE_NOT_IN_SOURCE", "cargo_description", "Cargo description is not stated in the e-mail")
    else:
        missing("cargo_description", "Cargo description")
    if raw.quantity is None:
        missing("quantity", "Quantity")
    elif not 0 < raw.quantity <= 2**63 - 1:
        issues.add("INVALID_QUANTITY", "quantity", "Quantity must be positive and fit a signed 64-bit integer")
    elif not _quantity_in_source(raw.quantity, lines):
        issues.add("VALUE_NOT_IN_SOURCE", "quantity", "Quantity is not an explicit unit count in the e-mail")
    else:
        out["quantity"] = raw.quantity

    # --- VINs ------------------------------------------------------------
    vins = [v.strip() for v in raw.vin_list if v.strip()]
    grounded = []
    for vin in vins:
        if _in_source(vin, source):
            grounded.append(vin)
        else:
            issues.add("VIN_NOT_IN_SOURCE", "vin_list", f"VIN {vin} does not occur in the e-mail")
    if len(set(grounded)) != len(grounded):
        issues.add("DUPLICATE_VIN", "vin_list", "The same VIN is listed more than once")
    if grounded and raw.quantity and raw.quantity > 0 and len(grounded) != raw.quantity:
        issues.add("VIN_COUNT_MISMATCH", "vin_list",
                   f"Quantity is {raw.quantity} but {len(grounded)} VINs are listed")
    if len(grounded) >= 3:
        shapes = [_vin_shape(v) for v in grounded]
        common = max(set(shapes), key=shapes.count)
        for vin, shape in zip(grounded, shapes):
            if shape != common or len(vin) != len(grounded[shapes.index(common)]):
                issues.add("VIN_FORMAT_UNUSUAL", "vin_list",
                           f"VIN {vin} does not follow the format of the other VINs in this booking")
    if not grounded and not partial:
        issues.add("VIN_MISSING", "vin_list", "No VINs are listed", Severity.WARNING)
    out["vin_list"] = grounded

    # --- weight ----------------------------------------------------------
    if raw.weight_text:
        if not _numbers_in_source(raw.weight_text, source):
            issues.add("VALUE_NOT_IN_SOURCE", "weight_kg_per_unit", "Weight number does not occur in the e-mail")
        else:
            weight = parse_weight(raw.weight_text)
            evidence = _source_weight(raw.weight_text, lines)
            if weight.code:
                issues.add(weight.code, "weight_kg_per_unit", weight.detail)
            elif evidence is None:
                issues.add("VALUE_NOT_IN_SOURCE", "weight_kg_per_unit", "No unambiguous source weight supports this value")
            elif evidence.code:
                issues.add(evidence.code, "weight_kg_per_unit", evidence.detail)
            elif evidence.kg != weight.kg:
                issues.add("VALUE_NOT_IN_SOURCE", "weight_kg_per_unit", "Model weight/unit differs from the source")
            elif raw.quantity not in (None, 1) and not evidence.per_unit:
                issues.add("WEIGHT_BASIS_UNCLEAR", "weight_kg_per_unit",
                           "Weight is not marked 'each'; it may be a total, so it is not stored per unit",
                           Severity.WARNING)
            else:
                out["weight_kg_per_unit"] = float(weight.kg)
    elif not partial:
        issues.add("WEIGHT_MISSING", "weight_kg_per_unit", "No weight is stated", Severity.WARNING)

    # --- dimensions ------------------------------------------------------
    if raw.dimensions_text:
        if not _numbers_in_source(raw.dimensions_text, source):
            issues.add("VALUE_NOT_IN_SOURCE", "dimensions", "Dimension numbers do not occur in the e-mail")
        else:
            dims = parse_dimensions(raw.dimensions_text)
            if dims.code:
                issues.add(dims.code, "dimensions", dims.detail)
            else:
                evidence = [parse_dimensions(phrase) for line in lines for phrase in dimension_expressions(line)
                            if number_tokens(phrase) == number_tokens(raw.dimensions_text)]
                expected = (dims.length_m, dims.width_m, dims.height_m)
                supported = [(item.length_m, item.width_m, item.height_m) for item in evidence if not item.code]
                if supported and set(supported) == {expected}:
                    out.update(length_m=dims.length_m, width_m=dims.width_m, height_m=dims.height_m)
                else:
                    issues.add("VALUE_NOT_IN_SOURCE", "dimensions", "Model dimensions/unit differ from the source")
    elif not partial:
        issues.add("DIMENSIONS_MISSING", "dimensions", "No dimensions are stated", Severity.WARNING)

    # --- date ------------------------------------------------------------
    if raw.requested_date_text and not _in_source(raw.requested_date_text, source):
        issues.add("VALUE_NOT_IN_SOURCE", "requested_date", "Date text does not occur in the e-mail")
    else:
        parsed = parse_requested_date(raw.requested_date_text)
        if parsed.kind == "exact":
            out["requested_date"] = parsed.value
            if parsed.value < email.received_at.date():
                issues.add("DATE_IN_PAST", "requested_date",
                           "Requested date is before the day the e-mail was received", Severity.WARNING)
        elif parsed.kind == "invalid":
            issues.add("INVALID_DATE", "requested_date", parsed.detail)
        elif parsed.kind == "lower_bound":
            issues.add("DATE_NOT_EXACT", "requested_date",
                       f"Only an earliest date ({parsed.value.isoformat()}) is given, not a sailing date")
        elif parsed.kind == "period":
            issues.add("AMBIGUOUS_DATE", "requested_date", parsed.detail)
        else:
            missing("requested_date", "Requested date")

    # --- pass-through, optional ------------------------------------------
    for field in ("vessel", "rate_text", "contact_name"):
        value = getattr(raw, field)
        if value and value.strip():
            if _in_source(value, source):
                out[field] = value.strip()
            else:
                issues.add("VALUE_NOT_IN_SOURCE", field, f"{field} is not stated in the e-mail")
    if raw.voyage and raw.voyage.strip():
        if not _in_source(raw.voyage, source):
            issues.add("VALUE_NOT_IN_SOURCE", "voyage", "Voyage is not stated in the e-mail")
        elif any(ch.isdigit() for ch in raw.voyage):
            out["voyage"] = raw.voyage.strip()
        else:
            issues.add("VOYAGE_UNRECOGNISED", "voyage",
                       f"'{raw.voyage.strip()}' does not look like a voyage number and was not stored", Severity.WARNING)
    if raw.contact_email:
        if _EMAIL.fullmatch(raw.contact_email.strip()):
            if _in_source(raw.contact_email, squash(source + " " + (email.sender or ""))):
                out["contact_email"] = raw.contact_email.strip()
            else:
                issues.add("VALUE_NOT_IN_SOURCE", "contact_email", "Contact e-mail is not stated in the source")
        else:
            issues.add("CONTACT_EMAIL_INVALID", "contact_email", "Contact e-mail is not a valid address", Severity.WARNING)

    blocked = email_blocked or any(i.severity == Severity.ERROR for i in issues.items)
    if partial:
        status = ProcessingStatus.PARSED
    else:
        status = ProcessingStatus.VALIDATION_REQUIRED if blocked else ProcessingStatus.READY
    draft = BookingDraft(message_intent=intent, source_email_id=email.email_id, processing_status=status, **out)
    return ValidatedBooking(booking_index=index, draft=draft, raw=raw, issues=tuple(issues.items))


def validate_extraction(email: EmailRecord, extraction: ExtractionResult, master: MasterIndex) -> ValidationResult:
    source = squash(" ".join([email.subject, email.clean_body, email.quoted_body]))
    lines = [*email.clean_body.splitlines(), *email.quoted_body.splitlines()]
    intent = extraction.intent
    email_issues = _Issues(None)

    if email.suspicious_text:
        email_issues.add("PROMPT_INJECTION_SUSPECTED", None,
                         "The e-mail contains instruction-like text that was withheld from the model; a person must review it")
    email_blocked = any(i.severity == Severity.ERROR for i in email_issues.items)

    raw_bookings = list(extraction.bookings)
    target: str | None = None
    action_intents = (MessageIntent.UPDATE_BOOKING, MessageIntent.CANCEL_BOOKING, MessageIntent.STATUS_INQUIRY)

    if intent in action_intents:
        candidate = extraction.target_booking_ref or next((b.booking_ref for b in raw_bookings if b.booking_ref), None)
        if candidate and _in_source(candidate, source):
            target = candidate.strip()
        elif candidate:
            email_issues.add("REFERENCE_NOT_IN_SOURCE", "target_booking_ref", "Referenced booking does not occur in the e-mail")
        if target is None:
            severity = Severity.WARNING if intent == MessageIntent.STATUS_INQUIRY else Severity.ERROR
            email_issues.add("MISSING_REFERENCE", "target_booking_ref",
                             "No booking reference: the request cannot be applied to any booking", severity)

    if intent == MessageIntent.CREATE_BOOKING:
        if not raw_bookings:
            email_issues.add("NO_BOOKING_EXTRACTED", None, "CREATE_BOOKING but no booking details were extracted")
        keep = raw_bookings
    elif intent == MessageIntent.UPDATE_BOOKING:
        keep = raw_bookings[:1]
        if not keep:
            email_issues.add("NO_CHANGES_EXTRACTED", None, "UPDATE_BOOKING but no changed values were extracted", Severity.WARNING)
    else:
        keep = []
        if raw_bookings:
            email_issues.add("BOOKINGS_IGNORED", None,
                             f"{intent.value} never creates bookings; extracted booking details were ignored", Severity.WARNING)

    validated = tuple(
        _validate_booking(raw, i, email, source, lines, master, intent, email_blocked,
                          tuple(extraction.unresolved_fields)) for i, raw in enumerate(keep)
    )
    return ValidationResult(
        email_id=email.email_id, intent=intent, bookings=validated, target_booking_ref=target,
        email_issues=tuple(email_issues.items), message_notes=extraction.message_notes,
    )
