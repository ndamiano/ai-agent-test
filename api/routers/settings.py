"""Settings API router for managing application settings"""

from fastapi import APIRouter, HTTPException
from config.settings_manager import settings_manager
from api.models.requests import UpdateSettingsRequest
from api.models.responses import SettingsResponse
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=SettingsResponse)
async def get_settings():
    """
    Get current application settings

    Returns:
        Current settings including connector type and connector-specific configurations
    """
    try:
        settings = settings_manager.get_settings()
        return SettingsResponse(**settings)
    except Exception as e:
        logger.error(f"Error retrieving settings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve settings: {str(e)}")


@router.put("", response_model=SettingsResponse)
async def update_settings(request: UpdateSettingsRequest):
    """
    Update application settings

    Args:
        request: Settings update request with new configuration

    Returns:
        Updated settings

    Raises:
        HTTPException: If settings validation fails or update fails
    """
    try:
        # Convert Pydantic models to dicts
        new_settings = {
            "connector_type": request.connector_type,
            "lmstudio": request.lmstudio.model_dump()
        }

        # Update settings (validation happens in settings_manager)
        updated_settings = settings_manager.update_settings(new_settings)

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
