# TeddyCloud Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.8%2B-brightgreen.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A native Home Assistant integration for [teddyCloud](https://github.com/toniebox-reverse-engineering/teddycloud)
— polls its REST API directly (no MQTT, no YAML). Creates real entities and
devices, one device per Toniebox known to your teddyCloud server.

> [!IMPORTANT]
> This project consists of **two components**:
> - **TeddyCloud Integration** (this repo) — entities/devices, install first
> - **[TeddyCloud Card](https://github.com/hypersonic30/ha-teddycloud-card)** — an optional Lovelace card that displays them nicely

## Why this exists

teddyCloud's own web UI runs on a different port/scheme than its REST API,
and the API doesn't send CORS headers — so a browser-side dashboard can't
call it directly. This integration polls the API server-side instead, and
exposes the results as normal HA entities.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories → add this repo's URL, category "Integration".
2. Install "TeddyCloud", restart Home Assistant.

### Manual

Copy `custom_components/teddycloud/` into your Home Assistant `config/custom_components/` directory
and restart Home Assistant.

## Setup

1. Settings → Devices & Services → Add Integration → search "TeddyCloud".
2. Enter:
   - **Host** — your teddyCloud server's hostname or IP, e.g. `192.168.1.10`
   - **Port** — the HTTP **API** port (commonly `7780`) — not the HTTPS web UI port, and not the
     Toniebox cloud-emulation port (`443`)
   - **Use HTTPS** / **Verify SSL certificate** — only if your teddyCloud API is served over HTTPS

Every Toniebox known to your teddyCloud server (`/api/getBoxes`) becomes its own HA device.
Adding a *new* box to teddyCloud later requires reloading this integration (or restarting HA)
to pick it up.

Entity names and IDs are fully yours to rename from Settings → Devices & Services →
TeddyCloud → the device → any entity, same as any other HA integration.

## Entities per box

| Entity | Type | Source |
|---|---|---|
| Online | binary_sensor | `internal.online` |
| Last Connection | sensor (timestamp) | `internal.last_connection` |
| Last IP | sensor | `internal.ip` |
| Current Tonie | sensor (+ cover as `entity_picture`) | `internal.last_ruid` → `getTagInfo` |
| Current Tonie Series | sensor | `internal.last_ruid` → `getTagInfo` |
| Tonie Library | sensor (count + full list w/ stream URLs as attribute) | `getTagIndex` |
| Cloud Enabled | switch | `cloud.enabled` |
| Cache Content | switch | `cloud.cacheContent` |
| Slap To Skip | switch | `toniebox.slap_enabled` |
| Slap Direction Forward On Left | switch | `toniebox.slap_back_left` |
| Max Volume Speaker | select (25/50/75/100%) | `toniebox.max_vol_spk` |
| Max Volume Headphones | select (25/50/75/100%) | `toniebox.max_vol_hdp` |
| LED Mode | select (on/off/dimmed) | `toniebox.led` |

Polling runs every 20 seconds.

## Audio streaming proxy

The Tonie Library sensor's audio URLs point at this integration's own
`/api/teddycloud/stream/...` endpoint rather than straight at teddyCloud. It
proxies the same bytes teddyCloud itself serves, only correcting the
`Content-Type` header (teddyCloud sends a generic one, which browsers ignore
in favor of the ha-teddycloud-card's own type hint, but which breaks
playback for anything that fetches the stream on its own — e.g. AirPlay to
a device on your local network). Nothing is buffered or stored: it's a
pure pass-through, streamed live, per request.

This endpoint deliberately doesn't require a Home Assistant login — the
same trust model as teddyCloud's own (also unauthenticated) download
endpoint it forwards to, and necessary since a playback target like an
AirPlay receiver has no HA session of its own. It only ever proxies to the
teddyCloud server configured for that config entry.

## Standalone player page

Each Tonie Library entry also carries a `player_url`
(`/api/teddycloud/player/<entry_id>/<box>/<ruid>`) alongside `audio_url` — a
minimal, self-contained HTML page (cover art, title, an `<audio>` element
pointed at the stream proxy above, and just enough JS to register the
[Media Session API](https://developer.mozilla.org/en-US/docs/Web/API/Media_Session_API)
for lock-screen controls) meant to be opened in its own browser tab. The
ha-teddycloud-card uses this rather than playing inline, since a full HA
dashboard is a heavy, actively-networking page that iOS is much less
willing to keep alive in the background than a bare, single-purpose one.

## Known limitations (by design, not a bug)

teddyCloud/the Toniebox protocol simply doesn't expose these, so they aren't built:

- **No battery percentage.** teddyCloud only knows charger on/off as an event, and only via
  MQTT/SSE — not exposed over REST at all.
- **No remote-play trigger.** The only server→box channel is the periodic freshness-check
  response, which carries volume/LED/slap settings — nothing that can start playback on a box
  without a physical Tonie figure placed on it.
- **No live playback progress/volume.** Those are MQTT/SSE-only events, not REST-pollable state.

## License

MIT
