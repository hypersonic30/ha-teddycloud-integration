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

### Instant-start playback (experimental — prerelease only)

Waiting for the whole file to download before starting playback is
reliable, but a real wait for long recordings. Prerelease versions (tagged
`vX.Y.Z`, marked as a GitHub prerelease so they're never offered as a
regular HACS update) try faster paths first, falling back to the proven
full-download approach if they don't pan out. Install a prerelease via
HACS's "Redownload" dialog (pick the version); roll back the same way if
it doesn't hold up.

**Native streaming (tried first).** The simplest possible approach: a
plain `<audio>` `<source>` pointed straight at the stream proxy, same as
teddyCloud's own web UI's player — no `fetch()`, no `Blob`, no
`MediaSource`. `stream_view.py` already forwards `Range` requests to
teddyCloud (confirmed against teddyCloud's own source: it really does
seek and serve partial content for cached files, not just accept the
header and ignore it), so the browser resolves duration and arbitrary
seeking itself, immediately, exactly like teddyCloud's own player does —
verified against a real Chromium instance seeking to the last few seconds
of a test file before anything beyond the first Range request had been
fetched. AirPlay needs no special handling either, since the source is
already a plain network URL rather than a `blob:` one.

What's unverified: whether this survives iOS backgrounding/lock screen.
The earlier finding that a live connection gets suspended by iOS was
made against the HA dashboard's *inline* player, where a live HA
websocket died at the same moment — it may have been that websocket, or
general page-script `fetch()` activity, that iOS was actually suspending,
not a native `<audio>` element's own network fetching (iOS has a
sanctioned "background audio playback" exemption for exactly that,
which is why browser-based podcast players keep working backgrounded).
If so, this path should hold up fine despite being live network audio —
only a real-device test settles it, which is the current open question.

Autoplay needs a tap on real iOS hardware: confirmed on a real device,
`audio.play()` rejects with WebKit's `NotAllowedError` for this path
specifically (duration/seeking still resolve correctly either way) —
consistent with the other two paths' source being a local `blob:` URL
(already-downloaded data) by the time `play()` runs, while this one is a
genuine network URL, which iOS Safari's autoplay-with-sound policy
doesn't extend the same allowance to in a freshly `window.open()`'d tab.
Not treated as a failure: the page keeps the native-stream source in
place and prompts for a tap instead of discarding it and falling back to
MSE, since a direct tap on the visible native play control is a fresh,
in-document gesture that succeeds immediately.

**MediaSource/WebM remux (fallback #1).** If native streaming's `<source>`
fails to load, `/api/teddycloud/remux/<entry_id>/<box>/<ruid>` remuxes
(not transcodes — `ffmpeg -c:a copy`, no re-encoding) teddyCloud's
Ogg/Opus into WebM as it downloads, and the player page progressively
appends it to a `MediaSource` (or, on iOS Safari 17.1+,
`ManagedMediaSource`). WebM, not MP4: an earlier version targeted
fragmented MP4 based on research claiming Safari 18.4 added Opus-in-MP4
support for MediaSource — wrong, confirmed by testing several MIME/codec
strings on real hardware (`ManagedMediaSource.isTypeSupported`):
`audio/mp4; codecs="opus"` → false, `audio/webm; codecs="opus"` → true.

Requires `ffmpeg` (bundled with Home Assistant OS and the official
Container image; not guaranteed elsewhere). Appending has to be throttled
and chunked to avoid `QuotaExceededError`, confirmed on real iOS hardware
in two distinct ways: appending as fast as data arrives overruns
`SourceBuffer`'s memory quota outright (fixed with a buffered-ahead cap),
and separately, a *single* `reader.read()` call from `fetch()` can itself
return a chunk of several megabytes (observed: ~18.5MB in one call)
regardless of how the read loop is paced, blowing the quota in one
`appendBuffer()` call even with inter-read throttling in place — fixed by
slicing every chunk into 64KB pieces before appending. Since teddyCloud's
API has no total-duration field at all (confirmed against its own
source — even its own web UI only learns duration by measuring the
browser's `<audio>` element after the full stream loads), the reported
duration grows to match what's actually been appended so far rather than
claiming to be a live stream; seeking ahead of the download isn't
possible either way, since the remux always starts from the beginning.

**Full download (fallback #2, the original, always-available path.)**
Downloads the whole file into memory before starting playback, trading a
wait up front for playback that needs no network at all once started.

What's *not* yet confirmed end-to-end on real iOS Safari over a real
network connection: whether native streaming survives backgrounding (the
main open question), and how `ManagedMediaSource`'s OS-driven buffer
eviction under memory pressure behaves in practice for the WebM fallback.

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
