"""Command-line behaviour a reviewer meets first: clear errors, and read-only commands that change nothing."""
import json
from support import *  # noqa: F401,F403
from ugr_booking.cli import main
from ugr_booking.db import make_engine


def test_ingest_without_the_dataset_says_where_to_put_it(tmp_path, capsys):
    missing = tmp_path / "no_such_package"
    assert main(["ingest", "--dataset", str(missing), "--db", f"sqlite:///{tmp_path / 'x.sqlite3'}"]) == 1
    error = json.loads(capsys.readouterr().err)["error"]
    assert "no_such_package" in error and "manifest.json" in error and "DATASET_PATH" in error
    assert not (tmp_path / "x.sqlite3").exists()          # nothing was created by the failed command


def test_read_only_commands_do_not_create_a_database(tmp_path, capsys):
    target = tmp_path / "never.sqlite3"
    for command in (["list-bookings"], ["report"]):
        assert main([*command, "--db", f"sqlite:///{target}"]) == 1
        error = capsys.readouterr().err
        assert "No database file" in error and "ingest" in error
    assert not target.exists()


def test_the_error_for_a_missing_database_names_the_file_and_the_fix(tmp_path, capsys):
    main(["list-bookings", "--db", f"sqlite:///{tmp_path / 'gone.sqlite3'}"])
    error = json.loads(capsys.readouterr().err)["error"]
    assert "gone.sqlite3" in error and "python -m ugr_booking.cli ingest" in error


def test_list_bookings_and_report_read_an_existing_database(tmp_path, capsys):
    database = f"sqlite:///{tmp_path / 'ok.sqlite3'}"
    orchestrator, engine, _ = make_orchestrator({"EXAMPLE-001": result(CREATE, bookings=[booking(booking_ref="REF-C1")])},
                                                engine=make_engine(database))
    from test_validation import BODY
    orchestrator.ingest([mail(1, with_ref("REF-C1", BODY))])
    engine.dispose()
    assert main(["list-bookings", "--events", "--db", database]) == 0
    listing = capsys.readouterr().out
    assert "BKG-00001" in listing and "REF-C1" in listing and "1 bookings, 1 e-mails" in listing and "CREATE" in listing
    assert main(["report", "--db", database]) == 0
    report = capsys.readouterr().out
    assert "| EXAMPLE-001 | CREATE_BOOKING | PROCESSED | BKG-00001 (READY)" in report
