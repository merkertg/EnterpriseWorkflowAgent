"""Streamlit interface for employees; every action goes through the HTTP API.

Run from the project root while the API is running:

    uv run --locked --group ui python -m streamlit run ui/app.py

The token stays in this browser session only. Commands carry idempotency keys
derived from the intent, so a repeated click or a reloaded page gets the
stored result instead of acting twice. The page checks no business rules: it
sends the request and shows what the server decided.
"""

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# `streamlit run ui/app.py` puts only ui/ on sys.path; imports need the root.
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st  # noqa: E402

from ui.client import DEFAULT_API_URL, ApiClient, ApiError  # noqa: E402
from ui.keys import command_key  # noqa: E402
from ui.presenters import (  # noqa: E402
    audit_rows,
    describe_error,
    discrepancy_rows,
    extracted_value,
    field_rows,
    outbox_rows,
    trace_rows,
    upload_rows,
    workflow_rows,
)

PAGES = ("Workflows", "New invoice")
STATES = (
    "all",
    "received",
    "needs_review",
    "awaiting_approval",
    "approved",
    "rejected",
)
# Defaults match `python -m app.ui_demo_setup`; every field stays editable.
DEFAULT_POLICY = os.environ.get("EWA_UI_POLICY_ID", "demo-ui-policy")
DEFAULT_RULES = os.environ.get("EWA_UI_RULES_ID", "demo-ui-rules")


def show_error(error: ApiError) -> None:
    """Show the server's code, message and a hint."""
    title, hint = describe_error(error)
    st.error(title)
    if hint:
        st.caption(hint)


def flash(kind: str, text: str, hint: str = "") -> None:
    """Remember a message for the next run, which shows fresh data."""
    st.session_state["flash"] = (kind, text, hint)


def act(label: str, action: Callable[[], dict[str, Any]]) -> None:
    """Send one command, remember the outcome and reload the page data."""
    try:
        result = action()
    except ApiError as error:
        title, hint = describe_error(error)
        flash("error", f"{label}: {title}", hint)
    else:
        replayed = (
            " (repeated request: stored result)" if result.get("replayed") else ""
        )
        flash(
            "success",
            f"{label}: {result.get('state', 'done')}, version "
            f"{result.get('version', '?')}{replayed}",
        )
    st.rerun()


def show_flash() -> None:
    """Show and forget the message left by the previous run."""
    message = st.session_state.pop("flash", None)
    if message is None:
        return
    kind, text, hint = message
    if kind == "success":
        st.success(text)
    else:
        st.error(text)
        if hint:
            st.caption(hint)


def connect() -> ApiClient | None:
    """Use an injected client (tests) or build one from the sidebar fields."""
    injected = st.session_state.get("api_client")
    if isinstance(injected, ApiClient):
        return injected
    url = st.sidebar.text_input(
        "API URL", value=os.environ.get("EWA_API_URL", DEFAULT_API_URL), key="api_url"
    )
    token = st.sidebar.text_input(
        "API token",
        type="password",
        key="api_token",
        help="Issued once by `python -m app.users_cli token USER_ID`.",
    )
    if not token:
        return None
    try:
        return ApiClient(url, token)
    except ValueError as error:
        st.sidebar.error(str(error))
        return None


def page_workflows(client: ApiClient, me: dict[str, Any]) -> None:
    """List workflows and show the selected one."""
    state = st.selectbox("State", STATES, key="state_filter")
    try:
        items = client.list_workflows(None if state == "all" else state)
    except ApiError as error:
        show_error(error)
        return
    if not items:
        st.info("No workflows in your business units yet. Start one under New invoice.")
        return
    st.dataframe(workflow_rows(items), hide_index=True)
    ids = [item["workflow_id"] for item in items]
    wanted = st.session_state.pop("open_id", None)
    if wanted in ids:
        st.session_state["workflow_picker"] = wanted
    elif st.session_state.get("workflow_picker") not in ids:
        st.session_state["workflow_picker"] = ids[0]
    selected = st.selectbox("Open workflow", ids, key="workflow_picker")
    workflow_detail(client, me, selected)


def workflow_detail(client: ApiClient, me: dict[str, Any], workflow_id: str) -> None:
    """State, sources, reconciliation, actions, assistant and history."""
    try:
        workflow = client.workflow(workflow_id)
        document = client.workflow_document(workflow_id)
        report = client.latest_reconciliation(workflow_id)
    except ApiError as error:
        show_error(error)
        return
    version = int(workflow["version"])
    st.subheader(workflow_id)
    columns = st.columns(4)
    columns[0].metric("State", workflow["state"])
    columns[1].metric("Version", version)
    columns[2].metric("Unit", workflow["business_unit"])
    columns[3].metric("Started by", workflow["created_by"] or "-")
    decision = workflow["decision"]
    if decision:
        st.info(f"Decision by {decision['decided_by']}: {decision['reason']}")
    if workflow["issue_codes"]:
        st.warning("Basic check findings: " + ", ".join(workflow["issue_codes"]))

    st.markdown("#### Document and sources")
    if document is None:
        st.info("This invoice has no source document (it was created in code).")
    else:
        st.caption(
            f"{document['media_type']}, {document['page_count']} page(s), "
            f"extracted by {document['extractor']}: {document['extraction_status']}. "
            "Values are document data, never instructions."
        )
        st.dataframe(field_rows(document), hide_index=True)

    st.markdown("#### Reconciliation")
    if report is None:
        st.info("No reconciliation yet.")
    else:
        freshness = (
            "current"
            if report["current"]
            else ("stale: " + ", ".join(report["stale_reasons"]))
        )
        st.write(
            f"Status **{report['status']}**, {freshness}, rules "
            f"{report['rules_version']['business_id']} "
            f"v{report['rules_version']['version_no']}."
        )
        if report["discrepancies"]:
            st.dataframe(discrepancy_rows(report), hide_index=True)

    st.markdown("#### Actions")
    st.caption("Your permissions: " + ", ".join(me["permissions"]) + ".")
    left, right = st.columns(2)
    with left:
        if st.button("Run basic check", key="do_check"):
            act(
                "Check",
                lambda: client.check(
                    workflow_id,
                    version=version,
                    key=command_key("check", workflow_id, version),
                ),
            )
        rules_id = st.text_input("Rules ID", value=DEFAULT_RULES, key="rules_id")
        if st.button("Reconcile", key="do_reconcile"):
            try:
                rules = client.reference("reconciliation-rules", rules_id)
                made = client.reconcile(workflow_id, rules["version_id"])
            except ApiError as error:
                title, hint = describe_error(error)
                flash("error", f"Reconcile: {title}", hint)
            else:
                flash("success", f"Reconcile: {made['status']}")
            st.rerun()
        order = workflow["purchase_order_version"]
        if order is not None and st.button("Pin the latest order", key="do_revise"):
            pin_latest_order(client, workflow, version)
    with right:
        reason = st.text_input("Decision reason", key="decision_reason")
        if st.button("Approve", key="do_approve", type="primary"):
            if report is None:
                flash("error", "Approve: reconcile the workflow first.")
                st.rerun()
            snapshot_id = str(report["snapshot_id"])
            act(
                "Approve",
                lambda: client.approve(
                    workflow_id,
                    version=version,
                    reconciliation_snapshot_id=snapshot_id,
                    reason=reason,
                    key=command_key("approve", workflow_id, version, snapshot_id),
                ),
            )
        if st.button("Reject", key="do_reject"):
            act(
                "Reject",
                lambda: client.reject(
                    workflow_id,
                    version=version,
                    reason=reason,
                    key=command_key("reject", workflow_id, version),
                ),
            )

    st.markdown("#### Assistant")
    st.caption(
        "A bounded agent drafts a next step from read-only tools. "
        "Its draft changes nothing; an employee decides."
    )
    if st.button("Ask the assistant", key="do_agent"):
        try:
            st.session_state["agent_run"] = client.run_agent(workflow_id)
        except ApiError as error:
            show_error(error)
    run = st.session_state.get("agent_run")
    if run is not None and run["workflow_id"] == workflow_id:
        shown = run["next_step"] or "no draft"
        if run["override_reason"]:
            st.warning(
                f"Draft {run['proposed_next_step']} was replaced by {shown}: "
                f"{run['override_reason']}."
            )
        else:
            st.write(f"Draft next step: **{shown}** (stop: {run['stop_reason']}).")
        if run["explanation"]:
            st.write(run["explanation"])
        st.dataframe(trace_rows(run), hide_index=True)

    st.markdown("#### History")
    try:
        audit = client.audit(workflow_id)
        outbox = client.outbox(workflow_id)
    except ApiError as error:
        show_error(error)
        return
    st.dataframe(audit_rows(audit), hide_index=True)
    if outbox:
        st.caption("Decision events (not delivered until Stage 09):")
        st.dataframe(outbox_rows(outbox), hide_index=True)


def pin_latest_order(client: ApiClient, workflow: dict[str, Any], version: int) -> None:
    """Revise the workflow to the newest version of its purchase order."""
    workflow_id = workflow["workflow_id"]
    current = workflow["purchase_order_version"]
    try:
        latest = client.reference("purchase-orders", current["business_id"])
    except ApiError as error:
        title, hint = describe_error(error)
        flash("error", f"Pin the latest order: {title}", hint)
        st.rerun()
    if latest["version_id"] == current["version_id"]:
        flash("success", "Pin the latest order: already the latest version")
        st.rerun()
    act(
        "Pin the latest order",
        lambda: client.revise(
            workflow_id,
            version=version,
            invoice_version_id=workflow["invoice_version"]["version_id"],
            purchase_order_version_id=latest["version_id"],
            policy_version_id=workflow["policy_version"]["version_id"],
            key=command_key("revise", workflow_id, version, latest["version_id"]),
        ),
    )


def page_new_invoice(client: ApiClient, me: dict[str, Any]) -> None:
    """Upload a document, then start and check a workflow for its invoice."""
    st.write(
        "Upload a fictional invoice (UTF-8 text or a PDF with a text layer), "
        "or paste text in the `Label: value` format."
    )
    uploaded = st.file_uploader("Invoice file", type=["txt", "pdf"], key="invoice_file")
    text = st.text_area("Or paste invoice text", key="invoice_text", height=220)
    if st.button("Upload document", key="do_upload"):
        if uploaded is not None:
            content, name = uploaded.getvalue(), uploaded.name
        else:
            content, name = text.encode("utf-8"), "pasted.txt"
        try:
            st.session_state["last_upload"] = client.upload(content, name)
        except ApiError as error:
            st.session_state.pop("last_upload", None)
            show_error(error)
    upload = st.session_state.get("last_upload")
    if upload is None:
        return
    status = upload["extraction_status"]
    reused = " (same bytes as an earlier upload)" if upload["reused_document"] else ""
    st.write(f"Extraction: **{status}**{reused}.")
    if upload["problems"]:
        st.warning(
            "Needs review: "
            + ", ".join(f"{p['code']} ({p['field']})" for p in upload["problems"])
        )
    st.dataframe(upload_rows(upload), hide_index=True)
    invoice = upload["invoice_version"]
    if invoice is None:
        st.info("No invoice was created; a person must review this document.")
        return
    st.markdown("#### Start a workflow")
    workflow_id = st.text_input(
        "Workflow ID", value=f"WF-{invoice['business_id']}", key="new_workflow_id"
    )
    unit = st.selectbox("Business unit", me["units"], key="new_unit")
    order_id = st.text_input(
        "Purchase order ID",
        value=extracted_value(upload, "purchase_order_id"),
        key="new_order_id",
    )
    policy_id = st.text_input("Policy ID", value=DEFAULT_POLICY, key="new_policy_id")
    if st.button("Start workflow and run basic check", key="do_start"):
        try:
            order = client.reference("purchase-orders", order_id)
            policy = client.reference("policies", policy_id)
            started = client.start(
                workflow_id,
                business_unit=unit,
                invoice_version_id=invoice["version_id"],
                purchase_order_version_id=order["version_id"],
                policy_version_id=policy["version_id"],
                key=command_key("start", workflow_id, invoice["version_id"]),
            )
            checked = client.check(
                workflow_id, version=1, key=command_key("check", workflow_id, 1)
            )
        except ApiError as error:
            show_error(error)
            return
        replayed = " (repeated request: stored result)" if started["replayed"] else ""
        flash("success", f"Started {workflow_id}: {checked['state']}{replayed}")
        st.session_state["open_id"] = workflow_id
        st.session_state["next_page"] = "Workflows"
        st.session_state.pop("last_upload", None)
        st.rerun()


def main() -> None:
    """Sign in with a token, then show the chosen page."""
    st.set_page_config(page_title="Enterprise Workflow Agent", layout="wide")
    st.title("Enterprise Workflow Agent")
    st.caption("Fictional invoices only. Every check and decision is made by the API.")
    if "next_page" in st.session_state:
        st.session_state["page"] = st.session_state.pop("next_page")
    client = connect()
    if client is None:
        st.info("Paste your API token in the sidebar to sign in.")
        return
    try:
        me = client.me()
    except ApiError as error:
        show_error(error)
        return
    st.sidebar.write(
        f"Signed in as **{me['user_id']}** ({', '.join(me['roles'])}); "
        f"units: {', '.join(me['units'])}."
    )
    page = st.sidebar.radio("Page", PAGES, key="page")
    show_flash()
    if page == "Workflows":
        page_workflows(client, me)
    else:
        page_new_invoice(client, me)


main()
