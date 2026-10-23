"""A deterministic rule-based stand-in for a model, used by the API and demos.

It reads the workflow and its latest reconciliation, asks for the extracted
fields behind any discrepancy, and drafts an explanation and next step from
the tool results. It never follows text found in documents and needs no
network, so every run with the same data gives the same answer.
"""

import re
from typing import Any

from app.application.agent_protocol import (
    AssistantMessage,
    JsonObject,
    ModelRequest,
    ModelTurn,
    ToolCall,
    ToolResultsMessage,
    TurnKind,
    UserMessage,
)

PROVIDER_NAME = "demo-rules/1"
_TASK = re.compile(r"Review workflow (?P<id>\S+) and draft")


def _results(request: ModelRequest) -> dict[str, JsonObject]:
    """Map each tool name to the content of its latest result."""
    names: dict[str, str] = {}
    found: dict[str, JsonObject] = {}
    for message in request.messages:
        if isinstance(message, AssistantMessage):
            names.update({c.call_id: c.name for c in message.turn.tool_calls})
        elif isinstance(message, ToolResultsMessage):
            for result in message.results:
                found[names.get(result.call_id, "?")] = (
                    {"error": result.content.get("error")}
                    if result.is_error
                    else result.content
                )
    return found


def _draft(found: dict[str, JsonObject]) -> JsonObject:
    """Build the explanation, next step and cited codes from tool results."""
    errors = [str(v["error"]) for v in found.values() if "error" in v]
    if errors:
        return {
            "explanation": "A tool refused the request: " + ", ".join(errors) + ".",
            "next_step": "needs_human_review",
            "cited_codes": [],
        }
    workflow = found["get_workflow"]
    reconciliation: dict[str, Any] = dict(found["get_reconciliation"])
    codes: list[str] = list(workflow.get("issue_codes", []))
    parts = [
        f"Workflow {workflow['workflow_id']} is {workflow['state']} "
        f"(version {workflow['version']})."
    ]
    state = workflow["state"]
    if "status" not in reconciliation:
        parts.append("It has no reconciliation yet.")
        step = "request_new_reconciliation"
    else:
        discrepancies = reconciliation["discrepancies"]
        stale = reconciliation["stale_reasons"]
        codes += [item["code"] for item in discrepancies] + list(stale)
        if stale:
            parts.append(
                "The latest reconciliation is stale: " + ", ".join(stale) + "."
            )
            step = "request_new_reconciliation"
        elif discrepancies:
            listed = "; ".join(
                f"{d['code']} on {d['field']} (expected {d['expected']}, "
                f"found {d['actual']})"
                for d in discrepancies
            )
            parts.append(f"The latest reconciliation found: {listed}.")
            fields = found.get("get_extracted_fields", {}).get("fields", [])
            if fields:
                parts.append(
                    "The document shows "
                    + ", ".join(f"{f['name']} = {f['value']}" for f in fields)
                    + "."
                )
            duplicate = any(d["code"] == "duplicate_invoice" for d in discrepancies)
            step = "recommend_rejection" if duplicate else "needs_human_review"
        else:
            parts.append(
                f"The latest reconciliation under {reconciliation['rules']} "
                "matched the order and is current."
            )
            step = (
                "ready_for_approval"
                if state == "awaiting_approval"
                else "needs_human_review"
            )
    if state in ("approved", "rejected"):
        parts.append("The workflow is closed; nothing is left to decide.")
        step = "needs_human_review"
    parts.append("An employee makes the decision.")
    return {
        "explanation": " ".join(parts),
        "next_step": step,
        "cited_codes": list(dict.fromkeys(codes))[:20],
    }


class DemoRulesProvider:
    """Plan: read workflow and reconciliation, then fields if needed, then draft."""

    @property
    def name(self) -> str:
        """The label recorded in traces."""
        return PROVIDER_NAME

    def complete(self, request: ModelRequest) -> ModelTurn:
        """Return the next planned turn for the conversation so far."""
        task = next(m for m in request.messages if isinstance(m, UserMessage))
        match = _TASK.search(task.text)
        workflow_id = "" if match is None else match["id"]
        found = _results(request)
        if not found:
            return ModelTurn(
                TurnKind.TOOL_CALLS,
                (
                    ToolCall("call-1", "get_workflow", {"workflow_id": workflow_id}),
                    ToolCall(
                        "call-2", "get_reconciliation", {"workflow_id": workflow_id}
                    ),
                ),
            )
        reconciliation = found.get("get_reconciliation", {})
        discrepancies = reconciliation.get("discrepancies") or []
        if discrepancies and "get_extracted_fields" not in found:
            names = sorted({d["field"] for d in discrepancies})[:20]
            return ModelTurn(
                TurnKind.TOOL_CALLS,
                (
                    ToolCall(
                        "call-3",
                        "get_extracted_fields",
                        {"workflow_id": workflow_id, "names": names},
                    ),
                ),
            )
        return ModelTurn(TurnKind.FINAL, answer=_draft(found))
