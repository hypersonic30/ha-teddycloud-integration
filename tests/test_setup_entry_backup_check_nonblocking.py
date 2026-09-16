"""Regression test for the startup-blocking bug: async_setup_entry used
to await the GitHub backup auto-import check inline, before forwarding
entry setup to the platforms - so a repo with several subfolders (each
costing a GitHub API round trip) directly delayed every entity showing
up. Fixed by scheduling it as a background task instead - this test
proves entry setup completes while that check is still deliberately
stuck mid-flight.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.teddycloud.const import (
    CONF_GITHUB_REPO,
    CONF_HOST,
    CONF_PORT,
    CONF_SIDECAR_URL,
    CONF_SSL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_setup_entry_does_not_block_on_the_startup_backup_check(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.10:7780",
        data={
            CONF_HOST: "192.168.1.10",
            CONF_PORT: 7780,
            CONF_SSL: False,
            CONF_VERIFY_SSL: True,
            CONF_SIDECAR_URL: "http://192.168.1.10:8000",
            CONF_GITHUB_REPO: "me/backups",
        },
    )
    entry.add_to_hass(hass)

    release = asyncio.Event()

    async def slow_import_check(coordinator):
        await release.wait()
        return 0

    with (
        patch(
            "custom_components.teddycloud.api.TeddyCloudApiClient.get_boxes",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.teddycloud.async_import_matching_wishlist_items",
            slow_import_check,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

        # Setup completed (entities forwarded, entry loaded) even though
        # the backgrounded backup check is still stuck on `release`.
        assert entry.state is ConfigEntryState.LOADED

        release.set()
        await hass.async_block_till_done()
