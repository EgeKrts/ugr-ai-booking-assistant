"""Controlled booking actions.

Every write goes through ToolExecutor.execute(), whether the call was planned by
fixed rules or chosen by the LLM. The executor enforces an allowlist, validates
arguments, checks that the tool fits the e-mail's intent and that any booking
reference is the one found in the e-mail. Data written always comes from the
validated draft, never from the arguments, so a model cannot inject field values.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from .db import BookingRow
from .master_data import MasterIndex
from .repository import COMPARED_FIELDS, UPDATABLE_FIELDS, Repository, comparable
from .schemas import (
    EmailRecord, MessageIntent, ProcessingStatus, Severity, ValidationIssue, ValidationResult,
    describe_validation_error,
)


class ToolStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    REVIEW_REQUIRED = "review_required"


class ToolResult(BaseModel):
    tool: str
    status: ToolStatus
    code: str
    message: str
    booking_id: int | None = None
    booking_ref: str | None = None
    event_type: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LookupCustomerArgs(_Args):
    customer_name: StrictStr = Field(min_length=1, max_length=200)


class LookupBookingArgs(_Args):
    booking_ref: StrictStr = Field(min_length=1, max_length=100)


class CreateBookingArgs(_Args):
    booking_index: StrictInt = Field(ge=0, description="Index of the validated booking candidate in this e-mail")


class UpdateBookingArgs(_Args):
    booking_ref: StrictStr = Field(min_length=1, max_length=100)


class CancelBookingArgs(_Args):
    booking_ref: StrictStr = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=1, max_length=200)


class RecordUnresolvedArgs(_Args):
    reason: StrictStr = Field(min_length=1, max_length=200)


@dataclass
class ToolSpec:
    args: type[_Args]
    description: str
    intents: frozenset[MessageIntent] | None   # None = any intent
    needs_reference: bool = False


ALL = None
_SPECS: dict[str, ToolSpec] = {
    "lookup_customer": ToolSpec(LookupCustomerArgs, "Check a customer name against the customer master data (read-only).", ALL),
    "lookup_booking": ToolSpec(LookupBookingArgs, "Look up an existing booking by its reference (read-only).", ALL, True),
    "create_booking": ToolSpec(
        CreateBookingArgs, "Store the validated booking candidate with this index as a new booking. "
        "Repeats of an existing booking are detected and not stored twice.",
        frozenset({MessageIntent.CREATE_BOOKING})),
    "update_booking": ToolSpec(
        UpdateBookingArgs, "Apply the validated changes from this e-mail to an existing booking.",
        frozenset({MessageIntent.UPDATE_BOOKING}), True),
    "cancel_booking": ToolSpec(
        CancelBookingArgs, "Cancel an existing booking.", frozenset({MessageIntent.CANCEL_BOOKING}), True),
    "record_unresolved_action": ToolSpec(
        RecordUnresolvedArgs, "Record that an update or cancellation cannot be applied (no usable booking "
        "reference, or the booking does not exist). Nothing is created.",
        frozenset({MessageIntent.UPDATE_BOOKING, MessageIntent.CANCEL_BOOKING})),
}
ALLOWED_TOOLS = tuple(_SPECS)


def tool_definitions(names: tuple[str, ...] | None = None) -> list[dict]:
    """Tool descriptions in the format chat models expect."""
    definitions = []
    for name in names or ALLOWED_TOOLS:
        spec = _SPECS[name]
        schema = spec.args.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        definitions.append({"type": "function", "function": {
            "name": name, "description": spec.description, "parameters": schema}})
    return definitions


@dataclass
class ToolContext:
    repo: Repository
    email: EmailRecord
    validation: ValidationResult
    created: dict[int, int] = field(default_factory=dict)       # booking_index -> booking id
    events: list[str] = field(default_factory=list)             # event types written in this e-mail
    created_statuses: list[str] = field(default_factory=list)


class ToolExecutor:
    def __init__(self, master: MasterIndex):
        self.master = master
        self._handlers: dict[str, Callable[[Any, ToolContext], ToolResult]] = {
            "lookup_customer": self._lookup_customer, "lookup_booking": self._lookup_booking,
            "create_booking": self._create, "update_booking": self._update,
            "cancel_booking": self._cancel, "record_unresolved_action": self._record_unresolved,
        }

    # ------------------------------------------------------------ policy gate
    def execute(self, name: str, arguments: Any, ctx: ToolContext) -> ToolResult:
        spec = _SPECS.get(name) if isinstance(name, str) else None
        if spec is None:
            return self._fail(str(name), "UNKNOWN_TOOL", "Tool is not on the allowlist")
        if not isinstance(arguments, dict):
            return self._fail(name, "ARGUMENT_INVALID", "Arguments must be a JSON object")
        try:
            args = spec.args.model_validate(arguments)
        except ValidationError as exc:
            return self._fail(name, "ARGUMENT_INVALID", describe_validation_error(exc))
        intent = ctx.validation.intent
        if spec.intents is not None and intent not in spec.intents:
            return self._fail(name, "TOOL_NOT_ALLOWED_FOR_INTENT", f"{name} may not be used for {intent.value}")
        if spec.needs_reference:
            target = ctx.validation.target_booking_ref
            if target is None or args.booking_ref.strip() != target:
                return self._fail(name, "REFERENCE_MISMATCH",
                                  "The booking reference is not the one stated in the e-mail")
        return self._handlers[name](args, ctx)

    @staticmethod
    def _fail(tool: str, code: str, message: str, status: ToolStatus = ToolStatus.FAILURE, **extra) -> ToolResult:
        return ToolResult(tool=tool, status=status, code=code, message=message, **extra)

    # ---------------------------------------------------------------- reads
    def _lookup_customer(self, args: LookupCustomerArgs, ctx: ToolContext) -> ToolResult:
        match = self.master.match_customer(args.customer_name)
        return ToolResult(tool="lookup_customer", status=ToolStatus.SUCCESS, code=match.status.upper(),
                          message=f"Customer lookup: {match.status}", data={"customer": match.value})

    def _lookup_booking(self, args: LookupBookingArgs, ctx: ToolContext) -> ToolResult:
        row = ctx.repo.booking_by_ref(args.booking_ref.strip())
        if row is None:
            return ToolResult(tool="lookup_booking", status=ToolStatus.SUCCESS, code="NOT_FOUND",
                              message=f"No booking with reference {args.booking_ref.strip()}",
                              booking_ref=args.booking_ref.strip(), data={"found": False})
        return ToolResult(tool="lookup_booking", status=ToolStatus.SUCCESS, code="FOUND",
                          message=f"{row.display_id} is {row.status}", booking_id=row.id, booking_ref=row.external_ref,
                          data={"found": True, "booking_id": row.display_id, "status": row.status,
                                "customer": row.customer, "pol": row.pol, "pod": row.pod, "quantity": row.quantity})

    # --------------------------------------------------------------- create
    def _create(self, args: CreateBookingArgs, ctx: ToolContext) -> ToolResult:
        bookings = ctx.validation.bookings
        if args.booking_index >= len(bookings):
            return self._fail("create_booking", "INVALID_BOOKING_INDEX", "No booking candidate with that index")
        if args.booking_index in ctx.created:
            return self._fail("create_booking", "ALREADY_APPLIED", "This candidate was already stored")
        candidate = bookings[args.booking_index]
        draft = candidate.draft
        existing = ctx.repo.booking_by_ref(draft.booking_ref) if draft.booking_ref else None
        if existing is None:
            existing = ctx.repo.booking_with_vins(draft.vin_list)
        if existing is not None:
            ctx.created.setdefault(args.booking_index, existing.id)
            return self._duplicate_or_conflict(existing, draft, args.booking_index, ctx)

        row = ctx.repo.add_booking(draft, candidate.raw)
        ctx.created[args.booking_index] = row.id
        ctx.created_statuses.append(row.status)
        ctx.repo.add_event("CREATE", ctx.email.email_id, booking_id=row.id,
                           reason=f"Created from {ctx.email.email_id}", details={"status": row.status})
        ctx.events.append("CREATE")
        if row.status == ProcessingStatus.READY.value:
            return ToolResult(tool="create_booking", status=ToolStatus.SUCCESS, code="CREATED",
                              message=f"{row.display_id} created and READY", booking_id=row.id,
                              booking_ref=row.external_ref, event_type="CREATE")
        return ToolResult(tool="create_booking", status=ToolStatus.REVIEW_REQUIRED, code="CREATED_FOR_REVIEW",
                          message=f"{row.display_id} stored as a draft that needs validation", booking_id=row.id,
                          booking_ref=row.external_ref, event_type="CREATE")

    def _duplicate_or_conflict(self, existing: BookingRow, draft, index: int, ctx: ToolContext) -> ToolResult:
        new, old = comparable(draft), comparable(existing)
        # Only values present on both sides can contradict each other.
        differing = [f for f in COMPARED_FIELDS if new[f] is not None and old[f] is not None and new[f] != old[f]]
        if draft.booking_ref and existing.external_ref and draft.booking_ref != existing.external_ref:
            differing.append("booking_ref")
        if existing.status == ProcessingStatus.CANCELLED.value:
            differing.append("status")
        if not differing:
            ctx.repo.add_event("DUPLICATE", ctx.email.email_id, booking_id=existing.id,
                               reason=f"{ctx.email.email_id} repeats {existing.display_id}; no new booking")
            ctx.events.append("DUPLICATE")
            return ToolResult(tool="create_booking", status=ToolStatus.SUCCESS, code="DUPLICATE_IGNORED",
                              message=f"Repeat of {existing.display_id}; nothing stored", booking_id=existing.id,
                              booking_ref=existing.external_ref, event_type="DUPLICATE")
        ctx.repo.add_event("FAILED_ACTION", ctx.email.email_id, booking_id=existing.id,
                           reason="CONFLICT_REQUIRES_REVIEW", details={"fields": differing})
        ctx.events.append("FAILED_ACTION")
        ctx.repo.add_issue(ctx.email.email_id, ValidationIssue(
            code="CONFLICT_REQUIRES_REVIEW", field=",".join(differing), severity=Severity.ERROR,
            message=f"Same booking as {existing.display_id} but differs in: {', '.join(differing)}",
            booking_index=index), existing.id)
        return ToolResult(tool="create_booking", status=ToolStatus.REVIEW_REQUIRED, code="CONFLICT_REQUIRES_REVIEW",
                          message=f"Conflicts with {existing.display_id} on {', '.join(differing)}; nothing overwritten",
                          booking_id=existing.id, booking_ref=existing.external_ref, event_type="FAILED_ACTION")

    # --------------------------------------------------------------- update
    def _update(self, args: UpdateBookingArgs, ctx: ToolContext) -> ToolResult:
        ref = args.booking_ref.strip()
        row = ctx.repo.booking_by_ref(ref)
        if row is None:
            return self._not_found("update_booking", ref, ctx)
        if row.status == ProcessingStatus.CANCELLED.value:
            return self._refuse("update_booking", "BOOKING_CANCELLED", f"{row.display_id} is cancelled", row, ctx)
        if not ctx.validation.bookings:
            return self._refuse("update_booking", "NO_CHANGES", "The e-mail contains no usable changes", row, ctx)
        candidate = ctx.validation.bookings[0]
        if candidate.has_errors:
            codes = ", ".join(sorted({i.code for i in candidate.issues if i.severity == Severity.ERROR}))
            return self._refuse("update_booking", "UPDATE_NOT_APPLIED",
                                f"The requested changes did not pass validation ({codes})", row, ctx,
                                status=ToolStatus.REVIEW_REQUIRED)
        draft = candidate.draft
        changes: dict[str, Any] = {}
        for name in UPDATABLE_FIELDS:
            if name == "dimensions_m":
                if None not in (draft.length_m, draft.width_m, draft.height_m):
                    changes[name] = {"length": draft.length_m, "width": draft.width_m, "height": draft.height_m}
                continue
            value = getattr(draft, name)
            if value not in (None, []):
                changes[name] = value
        diff = ctx.repo.apply_changes(row, changes)
        if not diff:
            return self._refuse("update_booking", "NO_CHANGES", "The e-mail changes nothing", row, ctx)
        ctx.repo.add_event("UPDATE", ctx.email.email_id, booking_id=row.id,
                           reason=f"Updated from {ctx.email.email_id}", details={"changes": diff})
        ctx.events.append("UPDATE")
        if row.quantity and row.vin_list and len(row.vin_list) != row.quantity:
            ctx.repo.set_status(row, ProcessingStatus.VALIDATION_REQUIRED.value)
            ctx.repo.add_issue(ctx.email.email_id, ValidationIssue(
                code="VIN_COUNT_MISMATCH", field="vin_list", severity=Severity.ERROR,
                message=f"After the update quantity is {row.quantity} but {len(row.vin_list)} VINs are stored"), row.id)
            return ToolResult(tool="update_booking", status=ToolStatus.REVIEW_REQUIRED, code="UPDATED_NEEDS_REVIEW",
                              message=f"{row.display_id} updated, but quantity and VIN list no longer agree",
                              booking_id=row.id, booking_ref=ref, event_type="UPDATE", data={"changes": diff})
        return ToolResult(tool="update_booking", status=ToolStatus.SUCCESS, code="UPDATED",
                          message=f"{row.display_id} updated: {', '.join(diff)}", booking_id=row.id,
                          booking_ref=ref, event_type="UPDATE", data={"changes": diff})

    # --------------------------------------------------------------- cancel
    def _cancel(self, args: CancelBookingArgs, ctx: ToolContext) -> ToolResult:
        ref = args.booking_ref.strip()
        row = ctx.repo.booking_by_ref(ref)
        if row is None:
            return self._not_found("cancel_booking", ref, ctx)
        if row.status == ProcessingStatus.CANCELLED.value:
            ctx.repo.add_event("DUPLICATE", ctx.email.email_id, booking_id=row.id,
                               reason=f"{row.display_id} was already cancelled")
            ctx.events.append("DUPLICATE")
            return ToolResult(tool="cancel_booking", status=ToolStatus.SUCCESS, code="ALREADY_CANCELLED",
                              message=f"{row.display_id} was already cancelled", booking_id=row.id,
                              booking_ref=ref, event_type="DUPLICATE")
        ctx.repo.set_status(row, ProcessingStatus.CANCELLED.value)
        ctx.repo.add_event("CANCEL", ctx.email.email_id, booking_id=row.id, reason=args.reason)
        ctx.events.append("CANCEL")
        return ToolResult(tool="cancel_booking", status=ToolStatus.SUCCESS, code="CANCELLED",
                          message=f"{row.display_id} cancelled", booking_id=row.id, booking_ref=ref,
                          event_type="CANCEL")

    # ------------------------------------------------------------ unresolved
    def _record_unresolved(self, args: RecordUnresolvedArgs, ctx: ToolContext) -> ToolResult:
        target = ctx.validation.target_booking_ref
        if target is not None and ctx.repo.booking_by_ref(target) is not None:
            return self._fail("record_unresolved_action", "ACTION_IS_RESOLVABLE",
                              f"Booking {target} exists, so the action is not unresolved")
        return self._unresolved("record_unresolved_action", args.reason, target, ctx)

    def _not_found(self, tool: str, ref: str, ctx: ToolContext) -> ToolResult:
        return self._unresolved(tool, f"BOOKING_NOT_FOUND: {ref}", ref, ctx, code="BOOKING_NOT_FOUND")

    def _unresolved(self, tool: str, reason: str, ref: str | None, ctx: ToolContext,
                    code: str = "UNRESOLVED_ACTION_RECORDED") -> ToolResult:
        ctx.repo.add_event("FAILED_ACTION", ctx.email.email_id, reason=reason,
                           details={"intent": ctx.validation.intent.value, "reference": ref})
        ctx.events.append("FAILED_ACTION")
        return ToolResult(tool=tool, status=ToolStatus.REVIEW_REQUIRED, code=code,
                          message=f"{ctx.validation.intent.value} not applied: {reason}", booking_ref=ref,
                          event_type="FAILED_ACTION")

    def _refuse(self, tool: str, code: str, message: str, row: BookingRow, ctx: ToolContext,
                status: ToolStatus = ToolStatus.FAILURE) -> ToolResult:
        ctx.repo.add_event("FAILED_ACTION", ctx.email.email_id, booking_id=row.id, reason=f"{code}: {message}")
        ctx.events.append("FAILED_ACTION")
        return ToolResult(tool=tool, status=status, code=code, message=message, booking_id=row.id,
                          booking_ref=row.external_ref, event_type="FAILED_ACTION")
