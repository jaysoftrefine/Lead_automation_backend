"""Backward-compatible proxy router for Email Campaigns API.

The routes have been refactored into modular sub-packages under `api.email`:
- `api.email.attachments`: Attachment upload and listing
- `api.email.smtp`: SMTP accounts CRUD and test connections
- `api.email.templates`: Templates CRUD, rendering preview, test emails
- `api.email.audiences`: Saved audiences & contact browsing
- `api.email.campaigns`: Campaign lifecycle, launch, logs, recipient preview
- `api.email.queue`: 1-by-1 review queue generation, AI regeneration, sending

This file re-exports `router` from `api.email` for 100% backward compatibility.
"""

from api.email import router

__all__ = ["router"]
