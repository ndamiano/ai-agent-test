"""Outputs router — serves generated files from inside the working directory only."""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter()


def _outputs_base() -> Path:
    from config.settings_manager import settings_manager
    return Path(settings_manager.get_settings()["working_directory"]).resolve()


@router.get("/{file_path:path}")
async def download_output_file(request: Request, file_path: str):
    """Download a generated file. The path must resolve inside the working directory —
    the server is network-reachable, so an unconstrained absolute path would expose the
    whole filesystem."""
    # Frontend strips the leading slash to avoid double-slash URLs; restore it.
    if not file_path.startswith("/"):
        file_path = "/" + file_path
    resolved = Path(file_path).resolve()

    base = _outputs_base()
    if not resolved.is_relative_to(base):
        raise HTTPException(status_code=403, detail="Path is outside the working directory")

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
