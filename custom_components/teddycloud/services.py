"""Service handlers for the TeddyCloud integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN
from .coordinator import TeddyCloudCoordinator
from .sidecar_api import SidecarApiError

_LOGGER = logging.getLogger(__name__)

SERVICE_ASSIGN_NFC_TAG = "assign_nfc_tag"
ATTR_FILE = "file"

ASSIGN_NFC_TAG_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_FILE): cv.string,
    }
)


def _resolve_box(hass: HomeAssistant, device_id: str) -> tuple[TeddyCloudCoordinator, str]:
    """Map a device_id (as registered by TeddyCloudBoxEntity) to its coordinator + box_id."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Unknown device: {device_id}")

    box_id = next((bid for (domain, bid) in device.identifiers if domain == DOMAIN), None)
    entry_id = next(iter(device.config_entries), None)
    if box_id is None or entry_id is None or entry_id not in hass.data.get(DOMAIN, {}):
        raise ServiceValidationError(f"Device {device_id} is not a TeddyCloud box")

    return hass.data[DOMAIN][entry_id], box_id


async def _async_assign_nfc_tag(hass: HomeAssistant, call: ServiceCall) -> None:
    for device_id in call.data[ATTR_DEVICE_ID]:
        coordinator, box_id = _resolve_box(hass, device_id)
        if coordinator.sidecar_client is None:
            raise ServiceValidationError(
                "No sidecar URL configured for this teddyCloud server — set one via "
                "Settings > Devices & services > TeddyCloud > Reconfigure."
            )

        with process_uploaded_file(hass, call.data[ATTR_FILE]) as file_path:
            content = file_path.read_bytes()
            filename = file_path.name

        try:
            result = await coordinator.sidecar_client.upload_nfc_tag(
                filename, content, overlay=box_id
            )
        except SidecarApiError as err:
            raise HomeAssistantError(f"nfc-bridge sidecar error: {err}") from err

        if not result.get("triggered"):
            _LOGGER.warning(
                "teddycloud: content.json written but download not triggered for %s: %s",
                box_id,
                result.get("message"),
            )

        await coordinator.async_request_refresh()


def async_register_services(hass: HomeAssistant) -> None:
    """Register domain-level services. Called once from async_setup."""
    if hass.services.has_service(DOMAIN, SERVICE_ASSIGN_NFC_TAG):
        return

    async def _handle(call: ServiceCall) -> None:
        await _async_assign_nfc_tag(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_ASSIGN_NFC_TAG, _handle, schema=ASSIGN_NFC_TAG_SCHEMA
    )
