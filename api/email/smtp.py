from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException

from schemas import (
    SMTPConfigBody,
    SMTPAccountCreate,
    SMTPAccountUpdate,
)
from email_campaigns.db import (
    get_smtp_config,
    save_smtp_config,
    list_smtp_accounts,
    get_smtp_account,
    create_smtp_account,
    update_smtp_account,
    delete_smtp_account,
    set_default_smtp_account,
    get_outreach_smtp_routing,
    save_outreach_smtp_routing,
)
from email_campaigns.smtp_sender import test_smtp_connection

router = APIRouter()


@router.get("/smtp/accounts")
def get_smtp_accounts() -> Dict[str, Any]:
    """Return list of all configured outgoing SMTP accounts with passwords masked."""
    accounts = list_smtp_accounts()
    for acc in accounts:
        acc["smtp_pass"] = "••••••••" if acc.get("smtp_pass") else ""
        acc["use_ssl"] = bool(acc.get("use_ssl"))
        acc["use_tls"] = bool(acc.get("use_tls"))
        acc["is_default"] = bool(acc.get("is_default"))
    return {"status": "success", "data": accounts}


@router.post("/smtp/accounts")
def add_smtp_account(body: SMTPAccountCreate) -> Dict[str, Any]:
    """Add a new SMTP account to the database."""
    created = create_smtp_account(body.model_dump())
    created["smtp_pass"] = "••••••••" if created.get("smtp_pass") else ""
    created["use_ssl"] = bool(created.get("use_ssl"))
    created["use_tls"] = bool(created.get("use_tls"))
    created["is_default"] = bool(created.get("is_default"))
    return {
        "status": "success",
        "message": f"SMTP account '{created.get('name')}' added successfully.",
        "data": created,
    }


@router.get("/smtp/accounts/{account_id}")
def get_single_smtp_account(account_id: str) -> Dict[str, Any]:
    """Retrieve details for a single SMTP account (password masked)."""
    acc = get_smtp_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="SMTP account not found.")
    acc["smtp_pass"] = "••••••••" if acc.get("smtp_pass") else ""
    acc["use_ssl"] = bool(acc.get("use_ssl"))
    acc["use_tls"] = bool(acc.get("use_tls"))
    acc["is_default"] = bool(acc.get("is_default"))
    return {"status": "success", "data": acc}


@router.put("/smtp/accounts/{account_id}")
def update_single_smtp_account(account_id: str, body: SMTPAccountUpdate) -> Dict[str, Any]:
    """Update an existing SMTP account. If password omitted or masked, existing password remains."""
    dump = body.model_dump(exclude_unset=True)
    updated = update_smtp_account(account_id, dump)
    if not updated:
        raise HTTPException(status_code=404, detail="SMTP account not found.")
    updated["smtp_pass"] = "••••••••" if updated.get("smtp_pass") else ""
    updated["use_ssl"] = bool(updated.get("use_ssl"))
    updated["use_tls"] = bool(updated.get("use_tls"))
    updated["is_default"] = bool(updated.get("is_default"))
    return {
        "status": "success",
        "message": f"SMTP account '{updated.get('name')}' updated successfully.",
        "data": updated,
    }


@router.delete("/smtp/accounts/{account_id}")
def delete_single_smtp_account(account_id: str) -> Dict[str, Any]:
    """Delete an SMTP account. If default, automatically selects another account."""
    ok = delete_smtp_account(account_id)
    if not ok:
        raise HTTPException(status_code=404, detail="SMTP account not found.")
    return {"status": "success", "message": "SMTP account removed."}


@router.post("/smtp/accounts/{account_id}/default")
def set_default_smtp_account_endpoint(account_id: str) -> Dict[str, Any]:
    """Set an SMTP account as the primary default account."""
    ok = set_default_smtp_account(account_id)
    if not ok:
        raise HTTPException(status_code=404, detail="SMTP account not found.")
    return {"status": "success", "message": "Account set as primary default SMTP."}


@router.post("/smtp/accounts/{account_id}/test")
def test_specific_smtp_account(account_id: str) -> Dict[str, Any]:
    """Test connection using a saved account's credentials."""
    cfg = get_smtp_config(account_id)
    if not cfg or not cfg.get("smtp_host") or not cfg.get("smtp_user"):
        raise HTTPException(status_code=404, detail="SMTP account not found or incomplete.")
    ok, msg = test_smtp_connection(cfg)
    return {
        "status": "success" if ok else "failed",
        "message": msg,
        "connected": ok,
        "account_id": account_id,
        "smtp_user": cfg.get("smtp_user"),
    }


@router.get("/smtp/config")
def get_smtp() -> Dict[str, Any]:
    """Return default SMTP configuration (password masked) for backward compatibility."""
    cfg = get_smtp_config()
    cfg["smtp_pass"] = "••••••••" if cfg.get("smtp_pass") else ""
    return {"status": "success", "data": cfg}


@router.post("/smtp/config")
def save_smtp(body: SMTPConfigBody) -> Dict[str, Any]:
    """Save default SMTP configuration to the database."""
    save_smtp_config(
        host=body.smtp_host,
        port=body.smtp_port,
        user=body.smtp_user,
        password=body.smtp_pass,
        from_name=body.from_name,
        use_ssl=body.use_ssl,
        use_tls=body.use_tls,
    )
    return {"status": "success", "message": "SMTP configuration saved successfully."}


@router.post("/smtp/test")
def test_smtp(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test SMTP connection with provided credentials or by account_id or default."""
    if body and body.get("account_id"):
        cfg = get_smtp_config(body["account_id"])
    elif body and body.get("smtp_host"):
        cfg = {
            "smtp_host": body.get("smtp_host", ""),
            "smtp_port": int(body.get("smtp_port") or 587),
            "smtp_user": body.get("smtp_user", ""),
            "smtp_pass": body.get("smtp_pass", ""),
            "from_name": body.get("from_name", "HirePilot AI"),
            "use_ssl": bool(body.get("use_ssl", False)),
            "use_tls": bool(body.get("use_tls", True)),
        }
    else:
        cfg = get_smtp_config()
    ok, msg = test_smtp_connection(cfg)
    return {"status": "success" if ok else "failed", "message": msg, "connected": ok}


@router.get("/smtp/outreach-routing")
def get_outreach_routing_endpoint() -> Dict[str, Any]:
    """Get active SMTP account assignments for Company and Freelancer outreach."""
    return {"status": "success", "data": get_outreach_smtp_routing()}


@router.post("/smtp/outreach-routing")
def save_outreach_routing_endpoint(body: Dict[str, Any]) -> Dict[str, Any]:
    """Set active SMTP account assignments and sending mode for outreach."""
    save_outreach_smtp_routing(
        company_smtp_account_id=body.get("company_smtp_account_id", ""),
        freelancer_smtp_account_id=body.get("freelancer_smtp_account_id", ""),
        company_smtp_account_ids=body.get("company_smtp_account_ids"),
        freelancer_smtp_account_ids=body.get("freelancer_smtp_account_ids"),
        sending_mode=body.get("sending_mode"),
        smtp_rotation=body.get("smtp_rotation"),
    )
    return {
        "status": "success",
        "message": "Outreach routing saved successfully.",
        "data": get_outreach_smtp_routing(),
    }
