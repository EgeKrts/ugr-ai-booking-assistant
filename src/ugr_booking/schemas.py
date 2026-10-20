"""Input contracts; schema validity does not mean business approval."""
from datetime import date, datetime
from enum import StrEnum
import re
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, ValidationError, field_validator

class MessageIntent(StrEnum):
    CREATE_BOOKING = "CREATE_BOOKING"
    UPDATE_BOOKING = "UPDATE_BOOKING"
    CANCEL_BOOKING = "CANCEL_BOOKING"
    STATUS_INQUIRY = "STATUS_INQUIRY"
    NON_BOOKING = "NON_BOOKING"
    UNKNOWN = "UNKNOWN"

class ProcessingStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PARSED = "PARSED"
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    CANCELLED = "CANCELLED"

class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

class ManifestEntry(InputModel):
    email_id: StrictStr = Field(min_length=1)
    file: StrictStr = Field(min_length=1)
    received_at: datetime
    subject: StrictStr

    @field_validator("received_at", mode="before")
    @classmethod
    def parse_received_at(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", value):
            raise ValueError("Use YYYY-MM-DD HH:MM without an assumed timezone")
        return datetime.strptime(value, "%Y-%m-%d %H:%M")

    @field_validator("email_id", "file")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank")
        return value

class EmailRecord(InputModel):
    """Parsed e-mail envelope plus original and cleaned body text.

    `clean_body` holds only the current message; explicitly quoted history is
    kept apart in `quoted_body` so it cannot be mistaken for the latest
    instruction. `warnings` are stable codes such as MISSING_SENDER.
    """
    email_id: StrictStr = Field(min_length=1)
    received_at: datetime
    subject: StrictStr
    sender: StrictStr | None = None
    raw_body: StrictStr
    clean_body: StrictStr
    quoted_body: StrictStr = ""
    suspicious_text: StrictStr = ""
    was_html: bool = False
    file_name: StrictStr = Field(min_length=1)
    warnings: tuple[StrictStr, ...] = ()

    @field_validator("email_id", "file_name")
    @classmethod
    def reject_blank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank")
        return value

class Customer(InputModel):
    customer_id: StrictStr = Field(min_length=1)
    name: StrictStr = Field(min_length=1)
    aliases: list[StrictStr]

class Port(InputModel):
    port_code: StrictStr = Field(min_length=1)
    name: StrictStr = Field(min_length=1)
    aliases: list[StrictStr]

class BookingDraft(InputModel):
    message_intent: MessageIntent = MessageIntent.UNKNOWN
    booking_ref: StrictStr | None = None
    customer: StrictStr | None = None
    pol: StrictStr | None = None
    pod: StrictStr | None = None
    cargo_description: StrictStr | None = None
    quantity: StrictInt | None = None
    vin_list: list[StrictStr] = Field(default_factory=list)
    weight_kg_per_unit: StrictFloat | None = None
    length_m: StrictFloat | None = None
    width_m: StrictFloat | None = None
    height_m: StrictFloat | None = None
    requested_date: date | None = None
    vessel: StrictStr | None = None
    voyage: StrictStr | None = None
    rate_text: StrictStr | None = None
    contact_name: StrictStr | None = None
    contact_email: StrictStr | None = None
    source_email_id: StrictStr = Field(min_length=1)
    processing_status: ProcessingStatus = ProcessingStatus.RECEIVED

    @field_validator("source_email_id")
    @classmethod
    def reject_blank_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Source e-mail identifier must not be blank")
        return value

    @field_validator("requested_date", mode="before")
    @classmethod
    def require_iso_date(cls, value):
        if value is None or type(value) is date:
            return value
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use YYYY-MM-DD or null; do not infer a date")
        return date.fromisoformat(value)


class ExtractedBooking(InputModel):
    """One booking candidate as written in the e-mail (LLM-facing).

    Values stay as close to the source text as possible. Unit conversion,
    date parsing and master-data matching happen later in deterministic
    validation, so nothing here is a business-approved value.
    """
    booking_ref: StrictStr | None = Field(default=None, description="Booking reference written in the e-mail, such as BOOKINGREF-A220; null if none.")
    customer: StrictStr | None = Field(default=None, description="Shipping customer / company the booking is for (usually in the signature or a Customer line).")
    pol: StrictStr | None = Field(default=None, description="Port of loading exactly as written (POL, from, ex).")
    pod: StrictStr | None = Field(default=None, description="Port of discharge exactly as written (POD, to, destination).")
    cargo_description: StrictStr | None = Field(default=None, description="What is shipped, as written (for example 3 USED HEAD TRUCKS).")
    quantity: StrictInt | None = Field(default=None, description="Number of units/vehicles requested, only if stated.")
    vin_list: list[StrictStr] = Field(default_factory=list, description="Every VIN or chassis number listed in the message, one string each.")
    weight_text: StrictStr | None = Field(default=None, description="Weight text exactly as written, with its unit or lack of unit (for example 8000 KG each).")
    dimensions_text: StrictStr | None = Field(default=None, description="Dimensions text exactly as written (for example 6 x 2.5 x 4 m).")
    requested_date_text: StrictStr | None = Field(default=None, description="Requested sailing date or period exactly as written; null if none.")
    vessel: StrictStr | None = Field(default=None, description="Vessel name if one is named.")
    voyage: StrictStr | None = Field(default=None, description="Voyage number if stated.")
    rate_text: StrictStr | None = Field(default=None, description="Rate or price text exactly as written (for example Rate EUR 1 / lm free out).")
    contact_name: StrictStr | None = Field(default=None, description="Name of the person who wrote or must be contacted, if given; not the company.")
    contact_email: StrictStr | None = Field(default=None, description="Contact e-mail address written in the body, if any.")
    notes: StrictStr | None = Field(default=None, max_length=200, description="One short sentence on anything unclear about this booking, or null.")


class ExtractionResult(InputModel):
    """Everything the model concluded about one e-mail, before any validation."""
    intent: MessageIntent
    bookings: list[ExtractedBooking] = Field(default_factory=list)
    target_booking_ref: StrictStr | None = None
    message_notes: StrictStr | None = Field(default=None, max_length=300, description="One short sentence (max 200 characters) on anything unclear or suspicious in the e-mail, or null.")
    unresolved_fields: list[StrictStr] = Field(default_factory=list, max_length=20, description="Names of needed fields the e-mail does not state.")

    def action_request(self) -> "ActionRequest | None":
        """UPDATE/CANCEL as an action on an existing booking; never a new booking."""
        if self.intent not in (MessageIntent.UPDATE_BOOKING, MessageIntent.CANCEL_BOOKING):
            return None
        return ActionRequest(
            intent=self.intent,
            target_booking_ref=self.target_booking_ref,
            changes=self.bookings[0] if self.bookings else None,
        )


class ActionRequest(InputModel):
    """A change request against an existing booking; unresolved if no reference."""
    intent: MessageIntent
    target_booking_ref: StrictStr | None = None
    changes: ExtractedBooking | None = None

    @property
    def is_resolvable(self) -> bool:
        return bool(self.target_booking_ref and self.target_booking_ref.strip())


def describe_validation_error(exc: ValidationError) -> str:
    """Readable one-line summary of a Pydantic error (no input values echoed)."""
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}"
        for e in exc.errors(include_input=False)
    )


class Severity(StrEnum):
    ERROR = "ERROR"      # blocks READY; a person must look at it
    WARNING = "WARNING"  # worth showing, does not block
    INFO = "INFO"


class ValidationIssue(InputModel):
    """A stable code plus the field it concerns; booking_index None means the whole e-mail."""
    code: StrictStr = Field(min_length=1)
    field: StrictStr | None = None
    message: StrictStr
    severity: Severity = Severity.ERROR
    booking_index: StrictInt | None = None


class ValidatedBooking(InputModel):
    """Trusted values only in `draft`; what the model wrote stays in `raw`."""
    booking_index: StrictInt
    draft: BookingDraft
    raw: ExtractedBooking
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(item.severity == Severity.ERROR for item in self.issues)


class ValidationResult(InputModel):
    email_id: StrictStr
    intent: MessageIntent
    bookings: tuple[ValidatedBooking, ...] = ()
    target_booking_ref: StrictStr | None = None
    email_issues: tuple[ValidationIssue, ...] = ()
    message_notes: StrictStr | None = None

    @property
    def all_issues(self) -> tuple[ValidationIssue, ...]:
        return self.email_issues + tuple(i for b in self.bookings for i in b.issues)

    @property
    def action_unresolved(self) -> bool:
        """UPDATE/CANCEL without a usable reference: record it, never create a booking."""
        return self.intent in (MessageIntent.UPDATE_BOOKING, MessageIntent.CANCEL_BOOKING) \
            and self.target_booking_ref is None
