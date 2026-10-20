"""SQLite persistence: four tables, created on first open and kept across restarts."""
from __future__ import annotations
from datetime import date, datetime
from pathlib import Path
from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DEFAULT_DATABASE_URL = "sqlite:///data/booking.sqlite3"


class Base(DeclarativeBase):
    pass


class EmailRow(Base):
    """One row per e-mail, with the original and cleaned text for traceability."""
    __tablename__ = "emails"
    email_id: Mapped[str] = mapped_column(String, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    subject: Mapped[str] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(String, nullable=True)
    file_name: Mapped[str] = mapped_column(String)
    raw_body: Mapped[str] = mapped_column(Text)
    clean_body: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, index=True)       # PROCESSED | REVIEW_REQUIRED | FAILED
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    extraction: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # what the model returned, for audit
    processed_at: Mapped[datetime] = mapped_column(DateTime)


class BookingRow(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_ref: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    customer: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    pol: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    pod: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    cargo_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    weight_kg_per_unit: Mapped[float | None] = mapped_column(Float, nullable=True)
    dimensions_m: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # {"length":..,"width":..,"height":..}
    vin_list: Mapped[list] = mapped_column(JSON, default=list)
    vessel: Mapped[str | None] = mapped_column(String, nullable=True)
    voyage: Mapped[str | None] = mapped_column(String, nullable=True)
    rate_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, index=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)           # values as the model wrote them
    source_email_id: Mapped[str] = mapped_column(ForeignKey("emails.email_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    @property
    def display_id(self) -> str:
        return f"BKG-{self.id:05d}"


class EventRow(Base):
    """CREATE / UPDATE / CANCEL / DUPLICATE / FAILED_ACTION, always tied to its source e-mail."""
    __tablename__ = "booking_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    source_email_id: Mapped[str] = mapped_column(ForeignKey("emails.email_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class IssueRow(Base):
    __tablename__ = "validation_issues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.email_id"), index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"), nullable=True, index=True)
    booking_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code: Mapped[str] = mapped_column(String, index=True)
    field: Mapped[str | None] = mapped_column(String, nullable=True)
    severity: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String, default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime)


def require_existing_database(url: str | None = None) -> str:
    """Return the URL, or raise if it is a SQLite file that does not exist yet.

    Read-only commands use this so that asking about an empty setup never creates an empty database file.
    """
    url = url or DEFAULT_DATABASE_URL
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        if not Path(parsed.database).is_file():
            raise ValueError(f"No database file at {parsed.database}. Create it first with: python -m ugr_booking.cli ingest")
    return url


def make_engine(url: str | None = None) -> Engine:
    """Open (and create if needed) the database; the parent folder is created for file databases."""
    url = url or DEFAULT_DATABASE_URL
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    if parsed.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return engine
