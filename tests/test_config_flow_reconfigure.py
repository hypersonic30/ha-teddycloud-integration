"""Regression test for the reconfigure bug: async_step_reconfigure used to
call _abort_if_unique_id_configured(), which aborts whenever the computed
unique ID matches *any* entry - including the very entry being
reconfigured, which it always does unless host/port changed. That made
every reconfigure attempt (e.g. just adding a GitHub repo, or a sidecar
URL) hit "already_configured", forcing delete-and-recreate to change any
setting. Fixed by switching to _abort_if_unique_id_mismatch(), the same
pattern HA core integrations (e.g. roku) use for their reconfigure flows.

Uses the real config-entries flow machinery (pytest-homeassistant-custom-
component's hass/MockConfigEntry) rather than calling the flow's methods
directly - the whole point is verifying our code interacts correctly with
HA's real reconfigure/unique-id framework, which a hand-rolled fake would
just paper over.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.teddycloud.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SIDECAR_URL,
    CONF_SSL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.10:7780",
        data={
            CONF_HOST: "192.168.1.10",
            CONF_PORT: 7780,
            CONF_SSL: False,
            CONF_VERIFY_SSL: True,
            CONF_SIDECAR_URL: "",
        },
    )


async def test_reconfigure_with_unchanged_host_port_succeeds(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.teddycloud.config_flow.TeddyCloudApiClient.get_boxes",
        AsyncMock(return_value=[]),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.1.10",
                CONF_PORT: 7780,
                CONF_SSL: False,
                CONF_VERIFY_SSL: True,
                CONF_SIDECAR_URL: "http://192.168.1.10:8000",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_SIDECAR_URL] == "http://192.168.1.10:8000"


async def test_reconfigure_pointing_at_a_different_server_is_rejected(hass):
    entry = _make_entry()
    entry.add_to_hass(hass)
    # A second, unrelated entry already occupies the host/port we're about
    # to try to reconfigure the first entry to point at.
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.20:7780",
        data={**entry.data, CONF_HOST: "192.168.1.20"},
    )
    other.add_to_hass(hass)

    with patch(
        "custom_components.teddycloud.config_flow.TeddyCloudApiClient.get_boxes",
        AsyncMock(return_value=[]),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.1.20",
                CONF_PORT: 7780,
                CONF_SSL: False,
                CONF_VERIFY_SSL: True,
                CONF_SIDECAR_URL: "",
            },
        )

    assert result["type"] == "abort"
    assert result["reason"] == "unique_id_mismatch"
