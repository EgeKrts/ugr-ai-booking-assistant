"""Input contracts; schema validity does not mean business approval."""
from datetime import date, datetime
from enum import StrEnum
import re
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator

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
