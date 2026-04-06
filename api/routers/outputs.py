"""Outputs router — serves generated files from the outputs/ directory."""

import mimetypes
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from api.rate_limiter import limiter

router = APIRouter()

_OUTPUTS_ROOT = Path("outputs").resolve()


def _safe_resolve(rel_path: str) -> Path:
    """Resolve a relative path under outputs/, rejecting path-traversal attempts."""
    # Normalise any encoded separators before resolution
    clean = urllib.parse.unquote(rel_path).lstrip("/")
    resolved = (_OUTPUTS_ROOT / clean).resolve()

    # Ensure the resolved path is still inside outputs/
    if not str(resolved).startswith(str(_OUTPUTS_ROOT)):
        raise HTTPException(status_code=400, detail="Invalid path")

    return resolved


@router.get("/{file_path:path}")
@limiter.limit("2/second")
async def download_output_file(request: Request, file_path: str):
    """
    Download a file from the outputs directory.

    The path should be relative to the outputs/ directory, e.g.:
      GET /api/outputs/report.md
      GET /api/outputs/data/results.csv

    Strips the leading "outputs/" prefix automatically so the frontend
    can pass artifact paths verbatim (e.g. "outputs/report.md").
    """
    # Strip "outputs/" prefix so both bare names and prefixed paths work
    normalised = file_path.lstrip("/")
    if normalised.startswith("outputs/"):
        normalised = normalised[len("outputs/"):]

    resolved = _safe_resolve(normalised)

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