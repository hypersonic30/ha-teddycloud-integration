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
minimal, self-contained HTML page (cover art, title, and just enough JS to
register the
[Media Session API](https://developer.mozilla.org/en-US/docs/Web/API/Media_Session_API)
for lock-screen controls) meant to be opened in its own browser tab. The
ha-teddycloud-card uses this rather than playing inline: two different
inline approaches were tried and both eventually got wiped out, together
with the whole player UI disappearing, a few minutes in — pointing at Home
Assistant's own frontend rebuilding the dashboard view (and every card in
it) after recovering from a websocket outage, unrelated to audio or
networking specifically. A standalone tab isn't part of that dashboard's
lifecycle at all, so rebuilding the dashboard can't touch it.

Being a bare page alone still wasn't fully reliable in practice — iOS can
suspend a backgrounded tab's network connections regardless of how little
else the page is doing, which cuts a live stream off mid-playback. So this
page downloads the whole file into memory before starting playback at all
(a wait up front, roughly file size ÷ connection speed) rather than
streaming it — once loaded, playback needs no network at all, so nothing
iOS does to the tab's connections in the background can interrupt it.

That local copy alone left AirPlay to another device broken, since a
receiver has to fetch the source itself and a browser-local `blob:` URL
has no network address to fetch. Fixed using
[WebKit's own documented pattern](https://webkit.org/blog/15036/how-to-use-media-source-extensions-with-airplay/)
for exactly this: the `<audio>` element gets two `<source>` children, the
local copy first (what actually plays) and this page's own stream proxy
URL second, purely as an AirPlay fallback. Safari transparently switches
to that second, fetchable URL when AirPlay is chosen — normal playback
never touches it.

### `dev` branch: instant-start playback (experimental)

The `main` branch's player page always waits for the whole file to download
before starting playback — reliable, but a real wait for long recordings.
This `dev` branch adds a second path, tried first: `/api/teddycloud/remux/
<entry_id>/<box>/<ruid>` remuxes (not transcodes — `ffmpeg -c:a copy`, no
re-encoding) teddyCloud's Ogg/Opus into fragmented MP4 as it downloads, and
the player page progressively appends it to a `MediaSource` (or, on iOS
Safari 17.1+, `ManagedMediaSource`) so playback can start almost
immediately. It falls back to the proven full-download approach wherever
MSE isn't usable — unsupported browser/codec, or any error partway
through — so this is additive, not a replacement.

Requires `ffmpeg` (bundled with Home Assistant OS and the official
Container image; not guaranteed elsewhere). The remux mechanics
(fragmentation, concurrent stdin/stdout piping, progressive delivery) and
the browser-side MediaSource/SourceBuffer playback have both been verified
against a real ffmpeg binary and a real Chromium instance — real audio
genuinely plays back progressively, not just in theory. What's *not* yet
verified on real hardware: Safari's `ManagedMediaSource` specifically,
which behaves differently from plain `MediaSource` (the OS can evict
buffered data under memory pressure) and which no test environment
available here can exercise.

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
