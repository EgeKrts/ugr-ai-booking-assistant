"""Booking operations dashboard. Run: streamlit run ui/app.py

Everything shown is read from the SQLite database on each interaction; no figure is typed in.
"""
from __future__ import annotations
from datetime import date
import os
from pathlib import Path
import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from ugr_booking.config import get_database_url, get_dataset_path
from ugr_booking.db import make_engine
from ugr_booking.chatbot import ChatService, KeywordPlanner
from ugr_booking.master_data import MasterIndex, load_dataset
from ugr_booking.query_tools import (
    UNKNOWN_LABEL, BookingFilter, ORDERS, bookings_requiring_review, count_bookings, count_by, email_overview, failed_actions,
    filter_options, get_booking_detail, open_issue_codes, search_bookings,
)

STATUS_COLORS = {"READY": "#2e7d32", "VALIDATION_REQUIRED": "#c77700", "CANCELLED": "#6b7280", "ASSIGNED": "#1565c0"}
ORDER_LABELS = {"latest": "Newest request first", "oldest": "Oldest request first",
                "requested_date": "Requested sailing date", "customer": "Customer A-Z"}

st.set_page_config(page_title="UGR Booking Operations", page_icon=None, layout="wide")


@st.cache_resource(show_spinner=False)
def engine_for(url: str):
    return make_engine(url)


@st.cache_data(show_spinner=False)
def port_names() -> dict[str, str]:
    """Port code -> name from the master data, when the dataset is available locally."""
    try:
        return {p.port_code: p.name for p in load_dataset(get_dataset_path()).ports}
    except Exception:
        return {}


def port_label(code: str | None) -> str:
    if not code:
        return "?"
    name = port_names().get(code)
    return f"{code} {name}" if name else code


def port_axis_label(value: str) -> str:
    return value if value == "(unknown)" else port_label(value)


EXAMPLE_QUESTIONS = (
    "How many bookings does ABC Shipping have?", "Show bookings with Izmir as the port of loading.",
    "Which bookings require validation?", "Summarize the latest three bookings.",
    "What is the status of BOOKINGREF-D100?", "Show cancelled bookings.",
    "Show bookings for a non-existing customer called Foo Corp.")


@st.cache_resource(show_spinner=False)
def chat_service(url: str):
    """The chatbot needs the master data (customer/port aliases); returns None if the package is missing."""
    try:
        master = MasterIndex.from_dataset(load_dataset(get_dataset_path()))
    except Exception:
        return None
    planner = KeywordPlanner(master) if os.environ.get("UGR_CHAT_PLANNER") == "keywords" else None
    return ChatService(engine_for(url), master, planner=planner)


def apply_to_bookings_tab(flt: BookingFilter, options: dict[str, list[str]]) -> None:
    """Copy a chat filter into the sidebar widgets (only values that exist in the data)."""
    keep = lambda values, key: [v for v in values if v in options[key]]
    st.session_state["f_customers"] = keep(flt.customers, "customers")
    st.session_state["f_pols"] = keep(flt.pols, "pols")
    st.session_state["f_pods"] = keep(flt.pods, "pods")
    st.session_state["f_ports"] = [p for p in flt.ports_either if p in options["pols"] or p in options["pods"]]
    st.session_state["f_statuses"] = keep(flt.statuses, "statuses")
    st.session_state["f_text"] = flt.text


def clear_chat_focus() -> None:
    reset_filters()
    st.session_state.pop("chat_focus", None)


def chat_tab(url: str) -> None:
    service = chat_service(url)
    st.caption("Answers come only from the booking database and are built from the matching rows. "
               "This assistant is read-only: it cannot change, create or cancel anything. "
               "Each answer also filters and highlights the dashboard.")
    if service is None:
        st.warning("The chat needs the master data (customer and port aliases). Put the dataset in "
                   "`data/private/dataset` or set `DATASET_PATH`.")
        return
    history = st.session_state.setdefault("chat", [])
    st.markdown("**Try a question**")
    for column, question in zip(st.columns(2), (EXAMPLE_QUESTIONS[:4], EXAMPLE_QUESTIONS[4:]), strict=False):
        for q in question:
            if column.button(q, key=f"ex_{q}", use_container_width=True):
                st.session_state["pending_question"] = q
    typed = st.chat_input("Ask about the bookings, for example: Show bookings with Izmir as the port of loading")
    question = typed or st.session_state.pop("pending_question", None)
    if question:
        with st.spinner("Reading the question and querying the database..."):
            answer = service.ask(question)
        history.append({"question": question, "answer": answer})
        # The dashboard follows the question: filters are applied before the sidebar is drawn on the next run.
        st.session_state["chat_focus"] = {"question": question, "ids": list(answer.booking_ids), "has_data": answer.has_data,
                                          "text": answer.text, "notes": list(answer.notes)}
        if answer.has_data and answer.filter is not None:
            st.session_state["pending_filter"] = answer.filter
        st.rerun()
    if history and st.button("Clear conversation", key="clear_chat"):
        st.session_state["chat"] = []
        clear_chat_focus()
        st.rerun()
    for turn in reversed(history):
        answer = turn["answer"]
        with st.chat_message("user"):
            st.write(turn["question"])
        with st.chat_message("assistant"):
            st.markdown(answer.text)
            for note in answer.notes:
                st.caption(note)
            with st.expander("How the question was read"):
                st.caption(f"Interpreted by: {answer.interpreter}")
                if answer.plan is not None:
                    st.json(answer.plan.model_dump(mode="json", exclude_none=True))


def database_ready(url: str) -> str | None:
    """Return an error message instead of silently creating an empty database file."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        if not Path(parsed.database).is_file():
            return (f"No database file at `{parsed.database}`. Create it with "
                    "`python -m ugr_booking.cli ingest` and reload this page.")
    return None


# ------------------------------------------------------------------ sidebar

def reset_filters() -> None:
    for key in ("f_customers", "f_pols", "f_pods", "f_ports", "f_statuses"):
        st.session_state[key] = []
    st.session_state["f_text"] = ""
    st.session_state["f_use_dates"] = False


def sidebar(options: dict[str, list[str]]) -> tuple[BookingFilter, str]:
    st.sidebar.header("Filters")
    customers = st.sidebar.multiselect("Customer", options["customers"], key="f_customers")
    pols = st.sidebar.multiselect("Port of loading", options["pols"], key="f_pols", format_func=port_label)
    pods = st.sidebar.multiselect("Port of discharge", options["pods"], key="f_pods", format_func=port_label)
    either_ports = sorted((set(options["pols"]) | set(options["pods"])) - {UNKNOWN_LABEL})
    ports = st.sidebar.multiselect("Port (loading or discharge)", either_ports, key="f_ports", format_func=port_label)
    statuses = st.sidebar.multiselect("Status", options["statuses"], key="f_statuses")
    text = st.sidebar.text_input("Search", key="f_text", placeholder="BOOKINGREF-D100, BKG-00004, cargo, e-mail id")
    date_from = date_to = None
    if st.sidebar.checkbox("Filter by requested date", key="f_use_dates"):
        picked = st.sidebar.date_input("Requested date range", value=(date(2026, 9, 1), date(2026, 12, 31)), key="f_dates")
        if isinstance(picked, tuple) and len(picked) == 2:
            date_from, date_to = picked
    order = st.sidebar.selectbox("Sort by", ORDERS, format_func=ORDER_LABELS.get, key="f_order")
    st.sidebar.button("Reset filters", on_click=reset_filters)
    flt = BookingFilter(tuple(customers), tuple(pols), tuple(pods), tuple(statuses), text, date_from, date_to,
                        ports_either=tuple(ports))
    return flt, order


# ------------------------------------------------------------------- pieces

def bar_chart(pairs: list[tuple[str, int]], title: str, *, status: bool = False, labels=None, limit: int = 12):
    if not pairs:
        st.caption(f"{title}: no data for the current filters")
        return
    frame = pd.DataFrame(pairs[:limit], columns=["name", "bookings"])
    frame["label"] = frame["name"].map(labels) if labels else frame["name"]
    color = (alt.Color("name:N", scale=alt.Scale(domain=list(STATUS_COLORS), range=list(STATUS_COLORS.values())), legend=None)
             if status else alt.value("#3b6fb6"))
    chart = (alt.Chart(frame, title=title).mark_bar()
             .encode(y=alt.Y("label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=240)), x=alt.X("bookings:Q", title="Bookings", axis=alt.Axis(tickMinStep=1)),
                     color=color, tooltip=["label", "bookings"])
             .properties(height=alt.Step(28)))
    st.altair_chart(chart, use_container_width=True)


def whole_numbers(frame: pd.DataFrame, column: str = "Qty") -> pd.DataFrame:
    """A missing quantity would turn the column into floats (2.000000); keep whole numbers and show gaps as blanks."""
    frame[column] = frame[column].astype("Int64")
    return frame


def booking_frame(views, issue_codes: dict[int, list[str]], highlight: frozenset[str] = frozenset()) -> pd.DataFrame:
    return whole_numbers(pd.DataFrame([{
        "Chat": "\u2605" if v.display_id in highlight else "",
        "": "REVIEW" if v.needs_review else ("CANCELLED" if v.status == "CANCELLED" else "ok"),
        "ID": v.display_id, "Reference": v.external_ref or "-", "Customer": v.customer or "?",
        "POL": v.pol or "?", "POD": v.pod or "?", "Qty": v.quantity, "Requested date": v.requested_date.isoformat() if v.requested_date else "?",
        "Status": v.status, "Source e-mail": v.source_email_id, "Received": v.received_at,
        "Open issues": ", ".join(issue_codes.get(v.id, [])),
    } for v in views]))


def style_rows(frame: pd.DataFrame):
    def row_style(row):
        if row["Chat"]:
            return ["background-color: rgba(59, 111, 182, 0.32); font-weight: 600"] * len(row)
        if row["Status"] == "VALIDATION_REQUIRED":
            return ["background-color: rgba(199, 119, 0, 0.18)"] * len(row)
        if row["Status"] == "CANCELLED":
            return ["color: rgba(128, 128, 128, 0.95); font-style: italic"] * len(row)
        return [""] * len(row)
    return frame.style.apply(row_style, axis=1)


def show_detail(session: Session, booking_id: int) -> None:
    detail = get_booking_detail(session, booking_id)
    if detail is None:
        st.warning("That booking no longer exists.")
        return
    v = detail.view
    st.subheader(f"{v.display_id} - {v.external_ref or 'no reference'} - {v.status}")
    left, right = st.columns([1, 1])
    with left:
        f = detail.fields
        dims = f["dimensions_m"]
        table = {
            "Customer": f["customer"], "POL": port_label(f["pol"]), "POD": port_label(f["pod"]),
            "Cargo": f["cargo_description"], "Quantity": f["quantity"], "Requested date": f["requested_date"],
            "Weight per unit (kg)": f["weight_kg_per_unit"],
            "Dimensions L x W x H (m)": f"{dims['length']} x {dims['width']} x {dims['height']}" if dims else None,
            "Vessel / voyage": " / ".join(x for x in (f["vessel"], f["voyage"]) if x) or None,
            "Rate (as written)": f["rate_text"], "Contact": " ".join(x for x in (f["contact_name"], f["contact_email"]) if x) or None,
        }
        st.dataframe(pd.DataFrame({"Field": list(table), "Value": ["-" if x is None else str(x) for x in table.values()]}),
                     hide_index=True, use_container_width=True)
        with st.expander(f"VINs ({len(f['vin_list'] or [])})"):
            st.write(f["vin_list"] or "none")
    with right:
        st.markdown("**Validation issues**")
        if detail.issues:
            st.dataframe(pd.DataFrame(detail.issues)[["scope", "severity", "code", "field", "message", "review_status"]],
                         hide_index=True, use_container_width=True)
        else:
            st.caption("No issues recorded.")
        st.markdown("**History**")
        st.dataframe(pd.DataFrame([{"event": e["event_type"], "e-mail": e["email_id"], "reason": e["reason"],
                                    "changes": e["details"]} for e in detail.events]),
                     hide_index=True, use_container_width=True)
    src = detail.source_email
    if src:
        with st.expander(f"Source e-mail {src['email_id']}: {src['subject']}"):
            st.caption(f"From {src['sender'] or 'unknown sender'} - received {src['received_at']:%Y-%m-%d %H:%M} - "
                       f"{src['intent']} - {src['status']}")
            st.code(src["text"] or "(empty body)", language=None)


# --------------------------------------------------------------------- page

def main() -> None:
    st.title("UGR Lines - Booking Operations")
    default_url = get_database_url()
    url = st.sidebar.text_input("Database", value=default_url, key="db_url")
    problem = database_ready(url)
    if problem:
        st.error(problem)
        st.stop()
    engine = engine_for(url)

    with Session(engine) as session:
        total_all = count_bookings(session)
        mails = email_overview(session)
        if total_all == 0:
            st.info("The database has no bookings yet. Run `python -m ugr_booking.cli ingest` to process the e-mails, "
                    "then reload this page.")
            st.caption(f"{mails['emails_total']} e-mails stored.")
            st.stop()

        options = filter_options(session)
        pending = st.session_state.pop("pending_filter", None)      # set by the chat; must happen before the widgets exist
        if pending is not None:
            apply_to_bookings_tab(pending, options)
        flt, order = sidebar(options)
        focus = st.session_state.get("chat_focus")
        highlight = frozenset(focus["ids"]) if focus else frozenset()
        views = search_bookings(session, flt, order=order)
        shown = len(views)
        by_status = dict(count_by(session, "status", flt))
        st.caption(f"Database: `{url}` - showing {shown} of {total_all} bookings"
                   + ("" if flt.is_empty else " (filters active)"))

        if focus:
            with st.container(border=True):
                top, clear = st.columns([5, 1])
                top.markdown(f"**Last chat question:** {focus['question']}")
                clear.button("Clear chat focus", on_click=clear_chat_focus, key="clear_focus")
                # short answers are shown; a long table starts collapsed so the dashboard stays in view
                with st.expander("Answer", expanded=len(focus["text"]) < 900):
                    st.markdown(focus["text"])
                for note in focus["notes"]:
                    st.caption(note)
                st.caption(f"The dashboard below follows this answer: filters applied, {len(focus['ids'])} booking(s) highlighted."
                           if focus["has_data"] else "No bookings matched, so the dashboard filters were left unchanged.")

        overview, table, attention, chat = st.tabs(["Overview", "Bookings", "Needs attention", "Chat"])

        with overview:
            cols = st.columns(4)
            cols[0].metric("Total bookings", shown)
            cols[1].metric("Ready", by_status.get("READY", 0))
            cols[2].metric("Needs validation", by_status.get("VALIDATION_REQUIRED", 0))
            cols[3].metric("Cancelled", by_status.get("CANCELLED", 0))
            st.caption("Booking figures follow the sidebar filters. E-mail figures below cover every e-mail processed.")
            cols = st.columns(4)
            non_booking = mails["by_intent"].get("NON_BOOKING", 0) + mails["by_intent"].get("UNKNOWN", 0)
            cols[0].metric("E-mails processed", mails["emails_total"])
            cols[1].metric("Non-booking e-mails", non_booking, help="Price talk, load lists, empty or unreadable messages. Never counted as bookings.")
            cols[2].metric("Status inquiries", mails["by_intent"].get("STATUS_INQUIRY", 0))
            cols[3].metric("Requests not applied", mails["failed_actions"],
                           help="Updates/cancellations with no known booking, and repeats that conflict with a stored booking.")
            left, right = st.columns(2)
            with left:
                bar_chart(count_by(session, "status", flt), "Bookings by status", status=True)
                bar_chart(count_by(session, "pol", flt), "By port of loading", labels=port_axis_label)
            with right:
                bar_chart(count_by(session, "customer", flt), "By customer")
                bar_chart(count_by(session, "pod", flt), "By port of discharge", labels=port_axis_label)
            bar_chart(count_by(session, "route", flt), "Busiest routes", limit=10)

        with table:
            if not views:
                st.warning("No bookings match these filters.")
                st.button("Reset filters", on_click=reset_filters, key="reset_empty")
            else:
                frame = booking_frame(views, open_issue_codes(session), highlight)
                st.caption("Amber rows need validation; grey italic rows are cancelled; blue rows marked \u2605 are the bookings from your last chat answer. Select a row for details.")
                picked = st.dataframe(style_rows(frame), hide_index=True, use_container_width=True,
                                      on_select="rerun", selection_mode="single-row", key="booking_table")
                rows = picked.selection.rows if picked is not None else []
                if rows:
                    show_detail(session, views[rows[0]].id)

        with attention:
            review = bookings_requiring_review(session, flt)
            st.subheader(f"Bookings that need validation ({len(review)})")
            if review:
                codes = open_issue_codes(session)
                st.dataframe(whole_numbers(pd.DataFrame([{
                    "Chat": "\u2605" if v.display_id in highlight else "", "ID": v.display_id, "Customer": v.customer or "?", "POL": v.pol or "?", "POD": v.pod or "?",
                    "Qty": v.quantity, "Why": ", ".join(codes.get(v.id, [])), "Source e-mail": v.source_email_id,
                } for v in review])), hide_index=True, use_container_width=True)
            else:
                st.caption("Nothing waiting for validation under the current filters.")
            st.subheader("Requests that could not be applied")
            actions = failed_actions(session)
            if actions:
                st.dataframe(pd.DataFrame(actions).rename(columns={"email_id": "E-mail", "received_at": "Received",
                             "intent": "Intent", "subject": "Subject", "reason": "Reason", "booking": "Booking"}),
                             hide_index=True, use_container_width=True)
            else:
                st.caption("None.")
            st.subheader("E-mails by detected intent")
            st.dataframe(pd.DataFrame(sorted(mails["by_intent"].items()), columns=["Intent", "E-mails"]),
                         hide_index=True, use_container_width=True)


        with chat:
            chat_tab(url)


main()
