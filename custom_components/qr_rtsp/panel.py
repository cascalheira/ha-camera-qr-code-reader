"""Register the custom admin panel and serve its frontend bundle."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    PANEL_ICON,
    PANEL_JS,
    PANEL_TITLE,
    PANEL_URL_PATH,
    PANEL_WEBCOMPONENT,
    STATIC_URL,
)

_LOGGER = logging.getLogger(__name__)


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the frontend bundle and register the sidebar panel (once)."""
    data = hass.data.setdefault(DOMAIN, {})
    frontend_dir = Path(__file__).parent / "frontend"

    if not data.get("static_registered"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(frontend_dir), False)]
        )
        data["static_registered"] = True

    if data.get("panel_registered"):
        return

    # Cache-bust the module URL by the file's mtime so updates always reload.
    try:
        version = int(os.path.getmtime(frontend_dir / PANEL_JS))
    except OSError:
        version = 0

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_WEBCOMPONENT,
        module_url=f"{STATIC_URL}/{PANEL_JS}?v={version}",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=True,
        config={},
    )
    data["panel_registered"] = True


@callback
def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel when the last entry is unloaded."""
    data = hass.data.get(DOMAIN, {})
    if data.get("panel_registered"):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        data["panel_registered"] = False
