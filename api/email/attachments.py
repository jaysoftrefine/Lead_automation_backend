import shutil
import uuid
from typing import Any, Dict
from fastapi import APIRouter, File, HTTPException, UploadFile

from api.email.shared import ATTACHMENTS_DIR

router = APIRouter()


@router.post("/attachments/upload")
async def upload_attachment(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Upload a PDF attachment and store it in uploads/attachments/."""
    original_filename = file.filename or "document.pdf"
    if not original_filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files (.pdf) are allowed as attachments.")

    file_uuid = uuid.uuid4().hex[:10]
    safe_filename = f"{file_uuid}_{original_filename.replace(' ', '_')}"
    destination = ATTACHMENTS_DIR / safe_filename

    try:
        with destination.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save attachment file: {str(e)}")

    file_size_kb = round(destination.stat().st_size / 1024, 1)

    return {
        "status": "success",
        "message": f"Attached {original_filename} ({file_size_kb} KB)",
        "data": {
            "attachment_name": original_filename,
            "attachment_path": str(destination),
            "file_size_kb": file_size_kb,
        },
    }


@router.get("/attachments")
def list_attachments() -> Dict[str, Any]:
    """List all available PDF attachments in uploads/attachments/."""
    items = []
    if ATTACHMENTS_DIR.exists():
        for p in sorted(ATTACHMENTS_DIR.glob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True):
            clean_name = p.name
            if "_" in clean_name and len(clean_name.split("_")[0]) == 10:
                clean_name = "_".join(clean_name.split("_")[1:])
            items.append({
                "filename": p.name,
                "display_name": clean_name,
                "path": str(p),
                "size_kb": round(p.stat().st_size / 1024, 1),
                "modified_at": int(p.stat().st_mtime),
            })
    return {"status": "success", "data": items}
