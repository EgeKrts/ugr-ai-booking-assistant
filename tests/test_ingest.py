"""Invented fixtures only; private dataset e-mail bodies are never committed."""
from datetime import datetime
import pytest
from ugr_booking.dataset import DatasetSource
from ugr_booking.ingest import (
    EmailParseError, decode_text, parse_email_text, read_dataset_emails, read_emails,
)
from ugr_booking.schemas import ManifestEntry
from test_dataset import package, write_json  # noqa: F401  (fixture reuse)

HEADER = "Email-ID: {id}\nFrom: a@example.test\nTo: b@example.test\nDate: {date}\nSubject: Invented\n"
SEP = "-" * 72 + "\n"


@pytest.fixture
def mail_package(package):
    """The shared fixture package, with every e-mail rewritten as a well-formed message."""
    import json
    for item in json.loads((package / "manifest.json").read_text(encoding="utf-8")):
        text = mail("Invented body", id=item["email_id"], date=item["received_at"])
        (package / item["file"]).write_text(text, encoding="utf-8")
    return package


def mail(body, *, id="EXAMPLE-000", date="2026-10-01 00:00", header=None):
    return (header or HEADER).format(id=id, date=date) + SEP + body


def entry(id="EXAMPLE-000", received="2026-10-01 00:00"):
    return ManifestEntry(email_id=id, file=f"emails/{id}.txt", received_at=received, subject="Invented")


def test_manifest_loads_34_unique_emails(mail_package):
    package = mail_package
    report = read_dataset_emails(package)
    assert report.manifest_total == 34
    assert len({r.email_id for r in report.records}) == 34
    assert not report.failures


def test_batch_returns_records_in_time_order(mail_package):
    package = mail_package
    # The fixture manifest is stored reversed; ingest must follow received_at.
    report = read_dataset_emails(package)
    assert [r.email_id for r in report.records] == sorted(r.email_id for r in report.records)


def test_manifest_order_may_differ_from_time_order(package):
    manifest = [entry("EXAMPLE-001", "2026-10-02 10:00"), entry("EXAMPLE-000", "2026-10-01 10:00")]
    ordered = tuple(sorted(manifest, key=lambda e: e.received_at))
    assert [e.email_id for e in ordered] == ["EXAMPLE-000", "EXAMPLE-001"]


def test_headers_and_body_are_parsed():
    record = parse_email_text(mail("Hello\nWorld\n"), entry())
    assert record.sender == "a@example.test"
    assert record.received_at == datetime(2026, 10, 1)
    assert record.clean_body == "Hello\nWorld"
    assert record.raw_body == "Hello\nWorld\n"
    assert record.warnings == ()


def test_empty_body_does_not_crash():
    record = parse_email_text(mail(""), entry())
    assert record.clean_body == ""
    assert "EMPTY_BODY" in record.warnings


def test_missing_sender_is_preserved_as_missing():
    header = "Email-ID: {id}\nTo: b@example.test\nDate: {date}\nSubject: Invented\n"
    record = parse_email_text(mail("Body", header=header), entry())
    assert record.sender is None
    assert "MISSING_SENDER" in record.warnings


def test_invalid_utf8_is_kept_not_fatal():
    text, warnings = decode_text(b"POL: SAG\xff\xfeUNTO\n")
    assert "�" in text and warnings == ["INVALID_UTF8_REPLACED"]
    record = parse_email_text(mail("POL: SAG�UNTO"), entry())
    assert "SUSPECT_ENCODING" in record.warnings
    assert "�" in record.raw_body  # the damaged value is not guessed or repaired


def test_broken_html_does_not_crash():
    body = "<html><body><div>Customer: X<br>\nPOL: A<br>\n<div><b>NOTE &nbsp;&nbsp; go\n</body>"
    record = parse_email_text(mail(body), entry())
    assert record.was_html
    assert "<" not in record.clean_body
    assert record.clean_body.splitlines()[:2] == ["Customer: X", "POL: A"]
    assert "\xa0" not in record.clean_body


def test_quoted_history_is_separated_from_current_instruction():
    body = ("LATEST: book 4 units\n\n----- Previous message -----\nbook 6 units\n"
            "----- End previous message -----\n\nThanks")
    record = parse_email_text(mail(body), entry())
    assert "6 units" not in record.clean_body and "4 units" in record.clean_body
    assert "Thanks" in record.clean_body
    assert record.quoted_body == "book 6 units"


def test_unterminated_quote_marker_quotes_the_rest():
    record = parse_email_text(mail("CANCEL X\n----- Original Message -----\nCREATE X\n"), entry())
    assert record.clean_body == "CANCEL X" and record.quoted_body == "CREATE X"


def test_ordinary_sentence_is_not_treated_as_quote():
    record = parse_email_text(mail("Original booking was 8 units.\nReduce to 6."), entry())
    assert record.quoted_body == "" and "Original booking" in record.clean_body


def test_manifest_header_mismatch_is_reported_not_fixed():
    record = parse_email_text(mail("Body", id="OTHER-999", date="2026-10-05 09:00"), entry())
    assert record.email_id == "EXAMPLE-000"
    assert {"EMAIL_ID_MISMATCH", "DATE_MISMATCH"} <= set(record.warnings)


def test_missing_separator_is_a_controlled_error():
    with pytest.raises(EmailParseError):
        parse_email_text("Email-ID: X\nno separator here", entry())


def test_one_bad_email_does_not_abort_batch(mail_package):
    package = mail_package
    (package / "emails/EXAMPLE-005.txt").write_text("garbage without separator", encoding="utf-8")
    damaged = mail("POL: SAG@@", id="EXAMPLE-006", date="2026-10-01 06:00").encode("utf-8")
    (package / "emails/EXAMPLE-006.txt").write_bytes(damaged.replace(b"@@", b"\xff\xfe"))
    report = read_dataset_emails(package)
    assert [f.email_id for f in report.failures] == ["EXAMPLE-005"]
    assert len(report.records) == 33  # the undecodable file still yields a flagged record
    assert report.summary()["unaccounted"] == 0
    flagged = next(r for r in report.records if r.email_id == "EXAMPLE-006")
    assert "INVALID_UTF8_REPLACED" in flagged.warnings


def test_every_manifest_entry_is_accounted_for(mail_package):
    package = mail_package
    with DatasetSource(package) as source:
        from ugr_booking.dataset import load_manifest
        report = read_emails(source, load_manifest(source, expected_count=34))
    assert len(report.records) + len(report.failures) == report.manifest_total == 34


# ---------------------------------------------------------------- inbox folder

from pathlib import Path  # noqa: E402
from ugr_booking.ingest import read_inbox_emails  # noqa: E402

DEMO_INBOX = Path(__file__).resolve().parent.parent / "examples" / "demo_inbox"


def write_mail(folder, name, email_id="INBOX-001", date="2026-10-12 09:00", body="Please cancel REF-1."):
    text = f"Email-ID: {email_id}\nFrom: a@example.test\nDate: {date}\nSubject: Invented\n{SEP}{body}\n"
    (folder / name).write_text(text, encoding="utf-8")


def test_inbox_folder_is_read_without_a_manifest(tmp_path):
    write_mail(tmp_path, "a.txt", "INBOX-001")
    write_mail(tmp_path, "b.txt", "INBOX-002", date="2026-10-12 10:30:15")
    report = read_inbox_emails(tmp_path)
    assert [r.email_id for r in report.records] == ["INBOX-001", "INBOX-002"] and report.failures == ()
    assert report.records[1].received_at == datetime(2026, 10, 12, 10, 30)


def test_a_bad_inbox_file_is_reported_and_the_others_still_load(tmp_path):
    write_mail(tmp_path, "good.txt")
    (tmp_path / "no_separator.txt").write_text("Email-ID: X\nDate: 2026-10-12 09:00\nno separator", encoding="utf-8")
    write_mail(tmp_path, "bad_date.txt", "INBOX-009", date="yesterday")
    (tmp_path / "ignored.md").write_text("not an e-mail", encoding="utf-8")
    report = read_inbox_emails(tmp_path)
    assert [r.email_id for r in report.records] == ["INBOX-001"]
    assert {f.file_name for f in report.failures} == {"no_separator.txt", "bad_date.txt"}
    assert report.manifest_total == 3


def test_duplicate_ids_in_the_inbox_are_refused(tmp_path):
    write_mail(tmp_path, "a.txt", "INBOX-001")
    write_mail(tmp_path, "b.txt", "INBOX-001")
    with pytest.raises(ValueError, match="same Email-ID"):
        read_inbox_emails(tmp_path)


def test_the_shipped_demo_inbox_loads_cleanly():
    report = read_inbox_emails(DEMO_INBOX)
    assert [r.email_id for r in report.records] == [f"DEMO-00{i}" for i in range(1, 7)] and report.failures == ()
    assert all(r.sender and r.clean_body and not r.warnings for r in report.records)
