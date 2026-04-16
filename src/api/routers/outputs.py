"""Outputs router — serves generated files by absolute filesystem path."""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/{file_path:path}")
async def download_output_file(request: Request, file_path: str):
    """Download a file by exact absolute filesystem path."""
    # Frontend strips the leading slash to avoid double-slash URLs; restore it.
    if not file_path.startswith("/"):
        file_path = "/" + file_path
    resolved = Path(file_path).resolve()

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    media_type, _ = mimetypes.guess_type(str(resolved))
    media_type = media_type or "application/octet-stream"

    return FileResponse(
        path=resolved,
        filename=resolved.name,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{resolved.name}"'},
    )
