"""Settings API router for managing application settings"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from config.settings_manager import settings_manager
from api.models.requests import UpdateSettingsRequest
from api.models.responses import SettingsResponse
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=SettingsResponse)
async def get_settings(request: Request):
    """
    Get current application settings

    Returns:
        Current settings including connector type and connector-specific configurations
    """
    try:
        from pathlib import Path
        settings = settings_manager.get_settings()
        # Ensure working_directory is an absolute path
        if settings.get('working_directory'):
            wd = Path(settings['working_directory'])
            if not wd.is_absolute():
                settings['working_directory'] = str(wd.resolve())
        else:
            # Default to absolute path of outputs directory
            settings['working_directory'] = str(Path('outputs').resolve())
        return SettingsResponse(**settings)
    except Exception as e:
        logger.error(f"Error retrieving settings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve settings: {str(e)}")


@router.put("", response_model=SettingsResponse)
async def update_settings(request: Request, settings_request: UpdateSettingsRequest):
    """
    Update application settings

    Args:
        settings_request: Settings update request with new configuration

    Returns:
        Updated settings

    Raises:
        HTTPException: If settings validation fails or update fails
    """
    try:
        from pathlib import Path

        # Convert Pydantic models to dicts
        new_settings = {
            "connector_type": settings_request.connector_type
        }

        if settings_request.working_directory:
            # Validate that working_directory is an absolute path
            wd = Path(settings_request.working_directory)
            if not wd.is_absolute():
                raise ValueError("working_directory must be an absolute path")
            new_settings["working_directory"] = settings_request.working_directory

        if settings_request.lmstudio:
            new_settings["lmstudio"] = settings_request.lmstudio.model_dump()

        if settings_request.cline:
            new_settings["cline"] = settings_request.cline.model_dump()

        # Update settings (validation happens in settings_manager)
        updated_settings = await asyncio.to_thread(
            settings_manager.update_settings, new_settings
        )

        # Note: Connector reinitialization happens in app.py when needed
        logger.info("Settings updated successfully")

        return SettingsResponse(**updated_settings)

    except ValueError as e:
        # Validation error
        logger.warning(f"Settings validation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Other errors
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update settings: {str(e)}")
