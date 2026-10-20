"""Run e-mails through extraction, validation and controlled actions, one transaction each.

Order of responsibility: the model reads (extractor), code checks (validators),
a planner picks tool calls (fixed rules, or the LLM), and ToolExecutor decides
whether each call is allowed and performs the only database writes.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
import json
import socket
from typing import Protocol
import urllib.error
import urllib.request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from .booking_tools import ToolContext, ToolExecutor, ToolResult, ToolStatus, tool_definitions
from .extractor import ExtractionError, Extractor, OllamaClient
from .master_data import MasterIndex
from .repository import Repository
from .schemas import EmailRecord, MessageIntent, Severity, ValidationResult
from .validators import validate_extraction

DONE_STATUSES = ("PROCESSED", "REVIEW_REQUIRED")   # a FAILED e-mail is retried on the next run
# Codes of tool calls the policy gate refused before anything was written.
REFUSED_CODES = frozenset({"UNKNOWN_TOOL", "ARGUMENT_INVALID", "TOOL_NOT_ALLOWED_FOR_INTENT",
                           "REFERENCE_MISMATCH", "ACTION_IS_RESOLVABLE", "ALREADY_APPLIED", "INVALID_BOOKING_INDEX"})


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict


class Planner(Protocol):
    def run(self, email: EmailRecord, validation: ValidationResult, executor: ToolExecutor,
            ctx: ToolContext) -> list[ToolResult]: ...


class DirectPlanner:
    """Fixed rules: the same intent always leads to the same tool calls."""

    def plan(self, validation: ValidationResult, email: EmailRecord) -> list[ToolCall]:
        intent, ref = validation.intent, validation.target_booking_ref
        if intent == MessageIntent.CREATE_BOOKING:
            return [ToolCall("create_booking", {"booking_index": b.booking_index}) for b in validation.bookings]
        if intent == MessageIntent.UPDATE_BOOKING:
            if ref is None:
                return [ToolCall("record_unresolved_action", {"reason": "MISSING_REFERENCE: no usable booking reference"})]
            return [ToolCall("update_booking", {"booking_ref": ref})]
        if intent == MessageIntent.CANCEL_BOOKING:
            if ref is None:
                return [ToolCall("record_unresolved_action", {"reason": "MISSING_REFERENCE: no usable booking reference"})]
            return [ToolCall("cancel_booking", {"booking_ref": ref, "reason": f"Cancelled by e-mail {email.email_id}"})]
        if intent == MessageIntent.STATUS_INQUIRY and ref is not None:
            return [ToolCall("lookup_booking", {"booking_ref": ref})]
        return []

    def run(self, email, validation, executor, ctx):
        return [_tagged(executor.execute(call.name, call.arguments, ctx), "rules") for call in self.plan(validation, email)]


def _tagged(result: ToolResult, via: str) -> ToolResult:
    """Record who chose the call: the language model ("model") or the fixed rules ("rules")."""
    result.data["via"] = via
    return result


LLM_PLANNER_PROMPT = """You operate a booking system. An e-mail was already read and checked by code; you only choose which tools to call.

Rules:
- Use only the tools provided. Pass only identifiers: a booking_index or the booking_ref given below. Never invent values.
- CREATE_BOOKING: call create_booking once per candidate index listed.
- UPDATE_BOOKING: if a reference is given, call lookup_booking first, then update_booking if it exists, otherwise record_unresolved_action. No reference: record_unresolved_action.
- CANCEL_BOOKING: same, with cancel_booking.
- STATUS_INQUIRY: call lookup_booking if a reference is given.
- NON_BOOKING and UNKNOWN: call no tool.
- record_unresolved_action is only for UPDATE_BOOKING and CANCEL_BOOKING. Never call it for any other intent.
- Once a tool result says the work is done (created, duplicate, cancelled, updated, found), stop and call nothing else.
After tool results arrive, call the next tool or stop when done."""


class LLMPlanner:
    """The model chooses the calls; ToolExecutor still checks every one of them."""

    def __init__(self, client: OllamaClient | None = None, max_rounds: int = 4):
        self.client = client or OllamaClient()
        self.max_rounds = max_rounds

    @staticmethod
    def _summary(email: EmailRecord, validation: ValidationResult) -> str:
        return json.dumps({
            "email_id": email.email_id,
            "intent": validation.intent.value,
            "booking_ref": validation.target_booking_ref,
            "candidates": [
                {"booking_index": b.booking_index, "status": b.draft.processing_status.value,
                 "customer": b.draft.customer, "pol": b.draft.pol, "pod": b.draft.pod,
                 "issue_codes": sorted({i.code for i in b.issues if i.severity == Severity.ERROR})}
                for b in validation.bookings],
            "email_issue_codes": [i.code for i in validation.email_issues],
        })

    def _chat(self, messages: list[dict]) -> dict:
        payload = {"model": self.client.model, "stream": False, "think": False,
                   "tools": tool_definitions(), "messages": messages,
                   "options": {"temperature": 0, "num_ctx": self.client.num_ctx}}
        request = urllib.request.Request(f"{self.client.base_url}/api/chat", json.dumps(payload).encode("utf-8"),
                                         {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.client.timeout) as response:
                return json.load(response)["message"]
        except (socket.timeout, TimeoutError) as exc:
            raise ExtractionError("LLM_TIMEOUT", "The tool-calling model did not answer in time") from exc
        except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError) as exc:
            raise ExtractionError("LLM_UNAVAILABLE", f"Tool-calling request failed: {exc}") from exc

    def run(self, email, validation, executor, ctx):
        messages = [{"role": "system", "content": LLM_PLANNER_PROMPT},
                    {"role": "user", "content": self._summary(email, validation)}]
        results: list[ToolResult] = []
        for _ in range(self.max_rounds):
            message = self._chat(messages)
            calls = message.get("tool_calls") or []
            if not calls:
                break
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            for call in calls:
                function = call.get("function", {})
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = None
                result = _tagged(executor.execute(function.get("name"), arguments, ctx), "model")
                results.append(result)
                messages.append({"role": "tool", "tool_name": function.get("name", ""),
                                 "content": result.model_dump_json()})
        return results


class GuardedLLMPlanner:
    """Default planner: the model chooses the tool calls, fixed rules finish what it left undone.

    Every call, whoever chose it, still goes through ToolExecutor's policy gate. The rules only run for work
    the model did not do (no tool called, a refused call, a candidate it skipped), so a weak or silent model
    can never make a booking request disappear. Each result says who chose it (`data["via"]`).
    """

    def __init__(self, llm: LLMPlanner | None = None, direct: DirectPlanner | None = None):
        self.llm = llm or LLMPlanner()
        self.direct = direct or DirectPlanner()

    def run(self, email, validation, executor, ctx):
        results = self.llm.run(email, validation, executor, ctx)      # a model outage propagates: the e-mail is retried later
        intent = validation.intent
        wanted = self.direct.plan(validation, email)
        acted = [r for r in results if r.code not in REFUSED_CODES]
        if intent == MessageIntent.CREATE_BOOKING:
            todo = [c for c in wanted if c.arguments["booking_index"] not in ctx.created]
        elif intent in (MessageIntent.UPDATE_BOOKING, MessageIntent.CANCEL_BOOKING):
            todo = [] if any(r.tool in ("update_booking", "cancel_booking", "record_unresolved_action") for r in acted) else wanted
        elif intent == MessageIntent.STATUS_INQUIRY:
            todo = [] if any(r.tool == "lookup_booking" for r in acted) else wanted
        else:
            todo = []
        return results + [_tagged(executor.execute(c.name, c.arguments, ctx), "rules") for c in todo]


@dataclass
class EmailOutcome:
    email_id: str
    intent: str | None = None
    status: str = "PROCESSED"
    skipped: bool = False
    error: str | None = None
    results: list[ToolResult] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    created_statuses: list[str] = field(default_factory=list)
    issue_codes: list[str] = field(default_factory=list)

    def line(self) -> str:
        if self.skipped:
            return f"{self.email_id}  skipped (already {self.status})"
        if self.error:
            return f"{self.email_id}  FAILED  {self.error}"
        actions = "; ".join(f"{r.tool}:{r.code}[{r.data.get('via', '?')}]" for r in self.results) or "no action"
        return f"{self.email_id}  {self.intent}  {self.status}  {actions}"


@dataclass
class BatchReport:
    outcomes: list[EmailOutcome] = field(default_factory=list)
    unreadable: int = 0          # files that could not be parsed into an e-mail at all

    def planner_stats(self) -> dict:
        """Who chose the tool calls: the model, or the rules (fixed planner, or finishing what the model left)."""
        calls = [r for o in self.outcomes for r in o.results]
        refused = [r for r in calls if r.code in REFUSED_CODES]
        effective = [r for r in calls if r.code not in REFUSED_CODES]
        return {"tool_calls_chosen_by_model": sum(r.data.get("via") == "model" for r in effective),
                "tool_calls_chosen_by_rules": sum(r.data.get("via") == "rules" for r in effective),
                "model_calls_refused_by_policy": len(refused)}

    def summary(self) -> dict:
        events = Counter(e for o in self.outcomes for e in o.events)
        created = Counter(s for o in self.outcomes for s in o.created_statuses)
        results = Counter(r.code for o in self.outcomes for r in o.results)
        done = [o for o in self.outcomes if not o.skipped]
        return {
            "email_total": len(self.outcomes) + self.unreadable,
            "processed": sum(1 for o in done if o.status in DONE_STATUSES),
            "failed": sum(1 for o in done if o.status == "FAILED") + self.unreadable,
            "skipped_already_processed": sum(1 for o in self.outcomes if o.skipped),
            "emails_needing_review": sum(1 for o in done if o.status == "REVIEW_REQUIRED"),
            "created": events["CREATE"],
            "updated": events["UPDATE"],
            "cancelled": events["CANCEL"],
            "duplicated": events["DUPLICATE"],
            "failed_actions": events["FAILED_ACTION"],
            "conflicts": results["CONFLICT_REQUIRES_REVIEW"],
            "validation_required": created["VALIDATION_REQUIRED"],
            "ready": created["READY"],
        }


class Orchestrator:
    def __init__(self, engine: Engine, extractor: Extractor, master: MasterIndex, planner: Planner | None = None):
        self.engine = engine
        self.extractor = extractor
        self.master = master
        self.planner = planner or DirectPlanner()
        self.executor = ToolExecutor(master)

    def ingest(self, emails: list[EmailRecord], on_email=None) -> BatchReport:
        report = BatchReport()
        for email in sorted(emails, key=lambda e: (e.received_at, e.email_id)):
            outcome = self.process_email(email)
            report.outcomes.append(outcome)
            if on_email:
                on_email(outcome)
        return report

    def process_email(self, email: EmailRecord) -> EmailOutcome:
        with Session(self.engine) as session:
            existing = Repository(session).get_email(email.email_id)
            if existing is not None and existing.status in DONE_STATUSES:
                return EmailOutcome(email.email_id, existing.intent, existing.status, skipped=True)

        try:
            extraction = self.extractor.extract(email)
        except ExtractionError as exc:
            return self._record_failure(email, None, str(exc))
        validation = validate_extraction(email, extraction, self.master)
        outcome = EmailOutcome(email.email_id, validation.intent.value)

        try:
            with Session(self.engine) as session, session.begin():
                repo = Repository(session)
                ctx = ToolContext(repo=repo, email=email, validation=validation)
                # The e-mail row comes first: events and issues point at it.
                repo.save_email(email, intent=validation.intent.value, status="PROCESSED",
                                extraction=extraction.model_dump(mode="json"))
                outcome.results = self.planner.run(email, validation, self.executor, ctx)
                for issue in validation.email_issues:
                    repo.add_issue(email.email_id, issue, None)
                for booking in validation.bookings:
                    booking_id = ctx.created.get(booking.booking_index)
                    for issue in booking.issues:
                        repo.add_issue(email.email_id, issue, booking_id)
                outcome.status = self._status(validation, outcome.results)
                outcome.events, outcome.created_statuses = ctx.events, ctx.created_statuses
                outcome.issue_codes = sorted({i.code for i in validation.all_issues})
                repo.save_email(email, intent=validation.intent.value, status=outcome.status,
                                outcome=self._outcome_text(outcome.results),
                                extraction=extraction.model_dump(mode="json"))
        except ExtractionError as exc:      # the tool-calling model failed mid-way: nothing was kept
            return self._record_failure(email, validation.intent.value, str(exc))
        except Exception as exc:            # rolled back by the transaction above
            return self._record_failure(email, validation.intent.value, f"{type(exc).__name__}: {exc}")
        return outcome

    @staticmethod
    def _status(validation: ValidationResult, results: list[ToolResult]) -> str:
        # A call the policy refused changed nothing, so it neither fixes nor spoils the e-mail's outcome.
        effective = [r for r in results if r.code not in REFUSED_CODES]
        if any(r.status != ToolStatus.SUCCESS for r in effective):
            return "REVIEW_REQUIRED"
        if validation.intent in (MessageIntent.CREATE_BOOKING, MessageIntent.UPDATE_BOOKING,
                                 MessageIntent.CANCEL_BOOKING) and not effective:
            return "REVIEW_REQUIRED"        # an actionable e-mail that led to no action
        if any(i.severity == Severity.ERROR for i in validation.all_issues):
            return "REVIEW_REQUIRED"
        return "PROCESSED"

    @staticmethod
    def _outcome_text(results: list[ToolResult]) -> str:
        parts = [f"[refused: {r.code}] {r.message}" if r.code in REFUSED_CODES else r.message for r in results]
        return " | ".join(parts) or "No action required"

    def _record_failure(self, email: EmailRecord, intent: str | None, error: str) -> EmailOutcome:
        with Session(self.engine) as session, session.begin():
            Repository(session).save_email(email, intent=intent, status="FAILED", error=error)
        return EmailOutcome(email.email_id, intent, "FAILED", error=error)
