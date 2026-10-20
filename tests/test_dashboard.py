"""Run the real Streamlit page headless against a temporary database."""
from pathlib import Path
import pytest
from sqlalchemy import text
from streamlit.testing.v1 import AppTest
from support import *  # noqa: F401,F403
from test_query_tools import entry
from ugr_booking.db import make_engine

APP = str(Path(__file__).resolve().parent.parent / "ui" / "app.py")


@pytest.fixture
def url(tmp_path):
    items = [entry(1, ref="REF-1"),
             entry(2, customer="Sample Freight", pol="IZMIR", pod="YANBU", vins=("TESTVIN0003", "TESTVIN0004")),
             entry(3, pod="YANBU", vins=("TESTVIN0005", "TESTVIN0006"), quantity=3)]     # quantity 3, two VINs: review
    script = {e.email_id: r for e, r in items}
    script["EXAMPLE-004"] = result(CANCEL, target="REF-1")
    script["EXAMPLE-005"] = result(NON_BOOKING)
    database = f"sqlite:///{tmp_path / 'dash.sqlite3'}"
    orchestrator, engine, _ = make_orchestrator(script, engine=make_engine(database))
    orchestrator.ingest([e for e, _ in items] + [mail(4, "Cancel REF-1"), mail(5, "Rates only")])
    engine.dispose()
    return database


def run(monkeypatch, url) -> AppTest:
    monkeypatch.setenv("DATABASE_URL", url)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def metrics(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def sql_scalar(url, query):
    engine = make_engine(url)
    with engine.connect() as connection:
        return connection.execute(text(query)).scalar()


def test_kpis_come_from_the_database_and_match_sql(monkeypatch, url):
    shown = metrics(run(monkeypatch, url))
    assert shown["Total bookings"] == str(sql_scalar(url, "SELECT COUNT(*) FROM bookings")) == "3"
    assert shown["Ready"] == str(sql_scalar(url, "SELECT COUNT(*) FROM bookings WHERE status='READY'")) == "1"
    assert shown["Needs validation"] == str(sql_scalar(url, "SELECT COUNT(*) FROM bookings WHERE status='VALIDATION_REQUIRED'"))
    assert shown["Cancelled"] == "1"
    assert shown["E-mails processed"] == "5"


def test_non_booking_emails_are_not_counted_as_bookings(monkeypatch, url):
    shown = metrics(run(monkeypatch, url))
    assert shown["Non-booking e-mails"] == "1" and shown["Total bookings"] == "3"


def test_unresolved_requests_counter_starts_at_zero_when_nothing_failed(monkeypatch, url):
    assert metrics(run(monkeypatch, url))["Requests not applied"] == "0"


def test_a_filter_changes_every_booking_figure(monkeypatch, url):
    at = run(monkeypatch, url)
    at.sidebar.multiselect(key="f_customers").select("Sample Freight").run()
    shown = metrics(at)
    assert shown["Total bookings"] == "1" and shown["Ready"] == "1" and shown["Cancelled"] == "0"
    assert "showing 1 of 3 bookings" in at.caption[0].value


def test_search_by_reference_and_reset_button(monkeypatch, url):
    at = run(monkeypatch, url)
    at.sidebar.text_input(key="f_text").input("REF-1").run()
    assert metrics(at)["Total bookings"] == "1" and metrics(at)["Cancelled"] == "1"
    next(b for b in at.sidebar.button if b.label == "Reset filters").click().run()
    assert metrics(at)["Total bookings"] == "3"


def test_filters_without_results_show_an_empty_state_not_an_error(monkeypatch, url):
    at = run(monkeypatch, url)
    at.sidebar.multiselect(key="f_customers").select("Sample Freight").run()
    at.sidebar.multiselect(key="f_statuses").select("CANCELLED").run()
    assert not at.exception
    assert metrics(at)["Total bookings"] == "0"
    assert any("No bookings match" in w.value for w in at.warning)


def test_review_rows_are_listed_with_their_reason(monkeypatch, url):
    at = run(monkeypatch, url)
    reasons = [df.value for df in at.dataframe]
    assert any("VIN_COUNT_MISMATCH" in frame.to_string() for frame in reasons)


def test_empty_database_shows_guidance(monkeypatch, tmp_path):
    database = f"sqlite:///{tmp_path / 'empty.sqlite3'}"
    make_engine(database).dispose()
    at = run(monkeypatch, database)
    assert any("no bookings yet" in i.value for i in at.info)


def test_missing_database_file_is_reported_and_not_created(monkeypatch, tmp_path):
    missing = tmp_path / "nothing.sqlite3"
    at = run(monkeypatch, f"sqlite:///{missing}")
    assert any("No database file" in e.value for e in at.error)
    assert not missing.exists()


# ---------------------------------------------------------------------- chat tab

@pytest.fixture
def chat_url(monkeypatch, url):
    from ugr_booking.config import get_dataset_path
    if not get_dataset_path().exists():
        pytest.skip("the dataset (master data) is not available locally")
    monkeypatch.setenv("UGR_CHAT_PLANNER", "keywords")     # no language model needed for these tests
    return url


def all_markdown(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_chat_answers_from_the_database_and_names_the_ids(monkeypatch, chat_url):
    at = run(monkeypatch, chat_url)
    at.chat_input[0].set_value("Show cancelled bookings").run()
    assert not at.exception
    text_ = all_markdown(at)
    assert "BKG-00001" in text_ and "status = CANCELLED" in text_


def test_chat_count_equals_the_dashboard_count(monkeypatch, chat_url):
    at = run(monkeypatch, chat_url)
    at.chat_input[0].set_value("How many bookings are there?").run()
    assert f"**{metrics(at)['Total bookings']}**" in all_markdown(at)


def test_chat_refuses_to_change_data(monkeypatch, chat_url):
    before = sql_scalar(chat_url, "SELECT COUNT(*) FROM bookings")
    at = run(monkeypatch, chat_url)
    at.chat_input[0].set_value("Delete all bookings").run()
    assert "cannot change data" in all_markdown(at)
    assert sql_scalar(chat_url, "SELECT COUNT(*) FROM bookings") == before
    assert sql_scalar(chat_url, "SELECT COUNT(*) FROM bookings WHERE status='CANCELLED'") == 1


def test_chat_filter_can_be_applied_to_the_bookings_tab(monkeypatch, chat_url):
    at = run(monkeypatch, chat_url)
    at.chat_input[0].set_value("Show cancelled bookings").run()
    next(b for b in at.button if "Bookings tab" in b.label).click().run()
    assert at.sidebar.multiselect(key="f_statuses").value == ["CANCELLED"]
    assert metrics(at)["Total bookings"] == "1"


def test_chat_example_questions_and_clear(monkeypatch, chat_url):
    at = run(monkeypatch, chat_url)
    next(b for b in at.button if b.label.startswith("Show cancelled bookings")).click().run()
    assert "BKG-00001" in all_markdown(at)
    next(b for b in at.button if b.label == "Clear conversation").click().run()
    assert len(at.chat_message) == 0
