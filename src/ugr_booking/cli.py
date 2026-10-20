"""Command-line entry points: inspect, parse, extract, validate, ingest and list."""
import argparse
import json
from pathlib import Path
import sys
import zipfile
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import get_database_url, get_dataset_path
from .dataset import DatasetError
from .db import BookingRow, EmailRow, EventRow, IssueRow, make_engine
from .extractor import ExtractionError, Extractor
from .ingest import read_dataset_emails
from .master_data import MasterIndex, load_dataset
from .query_tools import batch_report
from .orchestrator import DirectPlanner, LLMPlanner, Orchestrator
from .recorded import RecordedExtractor, record_extractions
from .validators import validate_extraction


def _dump(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _records(path: Path) -> dict:
    return {record.email_id: record for record in read_dataset_emails(path).records}


def _need(records: dict, email_id: str):
    if email_id not in records:
        raise ValueError(f"Unknown or unreadable e-mail: {email_id}")
    return records[email_id]


def _add_dataset(command) -> None:
    command.add_argument("--dataset", type=Path, help="Extracted dataset directory or original ZIP")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UGR booking utilities")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_dataset(commands.add_parser("inspect-dataset", help="Validate package metadata without processing e-mail bodies"))
    _add_dataset(commands.add_parser("parse-emails", help="Parse and clean every manifest e-mail; prints IDs and warning codes only"))
    extract = commands.add_parser("extract", help="Run the LLM on one e-mail and print the unvalidated result (nothing is saved)")
    extract.add_argument("email_id", help="For example EMAIL-017")
    _add_dataset(extract)
    validate = commands.add_parser("validate", help="Extract with the LLM, then apply the business rules (nothing is saved)")
    validate.add_argument("email_id", help="For example EMAIL-019")
    _add_dataset(validate)
    ingest = commands.add_parser("ingest", help="Process e-mails into the SQLite database (extract, validate, act)")
    _add_dataset(ingest)
    ingest.add_argument("--db", help="Database URL (default: DATABASE_URL or sqlite:///data/booking.sqlite3)")
    ingest.add_argument("--planner", choices=("direct", "llm"), default="direct",
                        help="direct = fixed rules choose the tool calls; llm = the model chooses them (still policy-checked)")
    ingest.add_argument("--only", nargs="+", metavar="EMAIL_ID", help="Process only these e-mails")
    ingest.add_argument("--extractions", type=Path, metavar="DIR",
                        help="Replay recorded model output instead of calling the model (offline demo / regression)")
    record = commands.add_parser("record-extractions", help="Run the model on every e-mail and save its output for later replay")
    _add_dataset(record)
    record.add_argument("--out", type=Path, default=Path("data/private/extractions"), help="Directory for the recording")
    report = commands.add_parser("report", help="Markdown report of every e-mail's outcome from the database")
    report.add_argument("--db", help="Database URL")
    listing = commands.add_parser("list-bookings", help="Show stored bookings, events and open issues")
    listing.add_argument("--db", help="Database URL")
    listing.add_argument("--events", action="store_true", help="Also list booking events")
    return parser


def _run_ingest(args, path: Path) -> int:
    ingest_report = read_dataset_emails(path)
    emails = list(ingest_report.records)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {e.email_id for e in emails}
        if unknown:
            raise ValueError(f"Unknown or unreadable e-mail(s): {', '.join(sorted(unknown))}")
        emails = [e for e in emails if e.email_id in wanted]
    master = MasterIndex.from_dataset(load_dataset(path))
    engine = make_engine(get_database_url(args.db))
    planner = LLMPlanner() if args.planner == "llm" else DirectPlanner()
    extractor = Extractor()
    if args.extractions:
        extractor = RecordedExtractor(args.extractions)
        print(f"*** REPLAY, not live AI: {extractor.describe()} ***", flush=True)
        print(flush=True)
    orchestrator = Orchestrator(engine, extractor, master, planner)
    report = orchestrator.ingest(emails, on_email=lambda o: print(o.line(), flush=True))
    report.unreadable = 0 if args.only else len(ingest_report.failures)
    print()
    _dump(report.summary())
    return 0 if report.summary()["failed"] == 0 else 1


def _list_bookings(args) -> int:
    engine = make_engine(get_database_url(args.db))
    with Session(engine) as session:
        bookings = session.scalars(select(BookingRow).order_by(BookingRow.id)).all()
        print(f"{'ID':<10}{'REF':<20}{'CUSTOMER':<22}{'POL>POD':<14}{'QTY':<5}{'DATE':<12}{'STATUS':<20}SOURCE")
        for b in bookings:
            print(f"{b.display_id:<10}{(b.external_ref or '-'):<20}{(b.customer or '?'):<22}"
                  f"{(b.pol or '?') + '>' + (b.pod or '?'):<14}{str(b.quantity or '?'):<5}"
                  f"{str(b.requested_date or '?'):<12}{b.status:<20}{b.source_email_id}")
        issues = session.scalars(select(IssueRow).where(IssueRow.review_status == "OPEN")).all()
        emails = session.scalars(select(EmailRow)).all()
        print(f"\n{len(bookings)} bookings, {len(emails)} e-mails, {len(issues)} open issues")
        if args.events:
            print()
            for e in session.scalars(select(EventRow).order_by(EventRow.id)):
                target = f"BKG-{e.booking_id:05d}" if e.booking_id else "-"
                print(f"{e.id:<4}{e.event_type:<14}{target:<10}{e.source_email_id:<11}{e.reason or ''}")
    return 0


def _report(args) -> int:
    engine = make_engine(get_database_url(args.db))
    with Session(engine) as session:
        rows = batch_report(session)
    print("| E-mail | Intent | Status | Bookings | Events | Blocking issues | Warnings |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['email_id']} | {r['intent']} | {r['status']} | {', '.join(r['bookings']) or '-'} | "
              f"{', '.join(r['events']) or '-'} | {', '.join(r['error_codes']) or '-'} | {', '.join(r['warnings']) or '-'} |")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list-bookings":
            return _list_bookings(args)
        if args.command == "report":
            return _report(args)
        path = get_dataset_path(args.dataset)
        if args.command == "ingest":
            return _run_ingest(args, path)
        if args.command == "record-extractions":
            emails = list(read_dataset_emails(path).records)
            def show(email_id, payload, seconds):
                print(f"{email_id}  {'ERROR ' + payload['error'] if 'error' in payload else payload['result']['intent']}  {seconds}s", flush=True)
            summary = record_extractions(emails, Extractor(), args.out, on_email=show)
            print()
            print(f"recorded {summary.recorded}, errors {summary.errors}, {summary.seconds}s -> {args.out}")
            return 0 if summary.errors == 0 else 1
        if args.command == "parse-emails":
            report = read_dataset_emails(path)
            _dump(report.summary())
            return 0 if report.manifest_total == len(report.records) + len(report.failures) else 1
        if args.command in ("extract", "validate"):
            record = _need(_records(path), args.email_id)
            try:
                extraction = Extractor().extract(record)
            except ExtractionError as exc:
                _dump({"email_id": record.email_id, "extraction_error": str(exc)})
                return 1
            if args.command == "extract":
                _dump({"email_id": record.email_id, "warnings": list(record.warnings),
                       "extraction": extraction.model_dump(mode="json")})
                return 0
            result = validate_extraction(record, extraction, MasterIndex.from_dataset(load_dataset(path)))
            _dump(result.model_dump(mode="json", exclude={"bookings": {"__all__": {"raw": True}}}, exclude_none=True))
            return 0
        _dump(load_dataset(path).summary())
        return 0
    except (DatasetError, OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
