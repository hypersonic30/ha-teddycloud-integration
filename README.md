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

Settings > Devices & services > TeddyCloud > ⋮ > Download diagnostics
gives a redacted dump (host, sidecar URL and each box's IP are stripped;
Tonie titles are left out entirely, just a library count) — useful to
attach to a bug report without fishing through logs.

## Audio streaming proxy

The Tonie Library sensor's audio URLs point at this integration's own
`/api/teddycloud/stream/...` endpoint rather than straight at teddyCloud.
It corrects the `Content-Type` header (teddyCloud sends a generic one,
which browsers ignore in favor of the ha-teddycloud-card's own type hint,
but which breaks playback for anything that fetches the stream on its
own — e.g. AirPlay to a device on your local network), and gives every
Tonie its own local cache (`content_cache.py`) rather than being a pure
pass-through:

- The first play of a Tonie starts one background download for it,
  shared by every request that comes in for that Tonie afterward — not
  just the one that started it. That matters because seeking aborts and
  replaces whichever connection a native `<audio>` element was using
  (confirmed — that's simply how seeking works); tying a download to one
  response's lifetime, an earlier version of this cache did, meant every
  seek restarted the whole download from byte zero, and — since a seek
  request could then only be answered once *that* entire re-download
  finished — even a jump to a point already downloaded, or nearly
  finished downloading, waited for a full redundant transfer first. Now
  playing from the start reads the shared download as it grows, and a
  seek anywhere else waits only until *that specific offset* has
  actually downloaded, not the whole file and not a second download.

  Known, accepted limitation: the download itself is still a single
  sequential fetch from byte 0 — deliberately never a Range request
  against teddyCloud, since that's exactly the bug this cache exists to
  avoid. So a seek ahead of however far that sequential download has
  currently reached still waits for it to get there; only a seek
  restarting the *whole* download (the actual bug fixed above) was
  avoidable without reintroducing risk. Fetching ahead-of-order with
  targeted Range requests would resolve this too, at the cost of
  bringing back the same upstream bug for whatever offsets those target
  — not worth it for what's normally just a brief wait during the first
  playthrough of a Tonie, gone entirely once it's fully cached.
- Every Range request (seeking) is served from that cache with a
  correct, from-scratch implementation, never forwarded to teddyCloud's
  own endpoint. That endpoint's embedded HTTP server has a real bug:
  with `skip_header=true` (required — the raw file isn't valid Ogg
  without it), seeking within roughly the last `TONIE_HEADER_LENGTH`
  bytes of a recording can silently return the wrong bytes while still
  claiming the requested range in its (already-sent) headers — confirmed
  on real iOS hardware via a `MEDIA_ERR_DECODE` at `readyState=4`
  (have-enough-data), i.e. a *successful* response with wrong content.
  Root cause in teddyCloud's own source:
  `src/cyclone/cyclone_tcp/http/http_server.c`, ~lines 1092-1157 — the
  Content-Range/Content-Length are computed from the client's requested
  offset *before* the header-length adjustment, but the later decision
  of whether to actually seek in the file compares the *already-adjusted*
  offset against the *unadjusted* total size. Fetching once and serving
  Range requests locally sidesteps it entirely (the same technique
  [github.com/ndeluigi/teddycloud-companion](https://github.com/ndeluigi/teddycloud-companion)'s
  own server uses). A handful of most-recently-played Tonies are kept
  (an LRU cache, capped rather than sized) — teddyCloud itself remains
  the actual source of truth.

All of the cache's own disk I/O runs through Home Assistant's executor
thread pool rather than directly in the event loop — confirmed on a real
deployment that this isn't just a style nitpick: Home Assistant's own
blocking-call detector caught a direct file write here, logged alongside
real playback stutters and failed seeks.

This endpoint deliberately doesn't require a Home Assistant login — the
same trust model as teddyCloud's own (also unauthenticated) download
endpoint it's sourced from, and necessary since a playback target like an
AirPlay receiver has no HA session of its own. It only ever proxies to the
teddyCloud server configured for that config entry.

Reassigning a physical tag to different content (the `assign_nfc_tag`
service below) invalidates that tag's cache entry, if any — otherwise a
listener would keep hearing whatever was cached under that ruid from
before the reassignment until it aged out on its own.

## Assigning a physical NFC tag to content

`assign_nfc_tag` uploads a `.nfc` dump (as produced by tools like a
Flipper Zero, or an app such as [SLI-Writer](https://github.com/Julienbxl/SLI-Writer))
to an optional [teddycloud-nfc-bridge](https://github.com/hypersonic30/teddycloud-nfc-bridge)
sidecar, which writes the corresponding `content.json` directly into
teddyCloud's own content volume. Configure the sidecar's URL via
Settings > Devices & services > TeddyCloud > Reconfigure; the service is
unavailable without one. The ha-teddycloud-card's optional "Assign NFC
tag" section (`show_nfc_assign: true`) is the easiest way to call it.

Browser-based NFC scanning (Web NFC) can't replace the `.nfc` file: a
tag's raw UID is all Web NFC (or even native Android/iOS NDEF APIs) can
read, but the sidecar also needs each tag's raw memory-block content
(`Data Content` in a Flipper `.nfc` file) for teddyCloud's cloud-auth
verification — genuine per-tag factory data, not derivable from the UID,
and reading it requires low-level ISO15693/MIFARE block access no browser
API exposes, on any platform.

## Wishlist

Each box tracks a small wishlist, stored locally (not in entity state —
see `wishlist.py`), of Tonies you don't have yet. The ha-teddycloud-card's
wishlist section lets you search teddyCloud's full `tonies.json` catalog
and add an entry; it disappears from the "still wanted" view on its own
once that Tonie shows up in the box's library (matched by `tonies.json`'s
own model ID). This part is unchanged by everything below.

### Auto-restoring wishlist items from a GitHub backup repo

If you keep a backup set of `.nfc` tag dumps in a GitHub repo, the
integration can automatically restore a wishlist item once a matching
backup is found — useful for recovering after your teddyCloud instance
loses its tag assignments: re-add the Tonies you want (or they're
already on the list) and let the integration quietly re-trigger the
download for whichever ones it finds a backup for, the same way
[Assigning a physical NFC tag to content](#assigning-a-physical-nfc-tag-to-content)
does manually for one file. Configure a repo under Settings > Devices &
services > TeddyCloud > Reconfigure:

- **NFC backup GitHub repo** (`owner/repo`) — leave empty to disable the
  feature entirely.
- **Branch** — defaults to `main`.
- **Subfolder** — optional; leave empty to use the repo root.
- **Token** — a GitHub personal access token with read access. Required
  for a private repo; optional for a public one, but recommended anyway
  to raise GitHub's API rate limit (60/hour unauthenticated vs.
  5000/hour with a token). **A `.nfc` dump's memory content is
  effectively a credential for that tag's cloud content** (see
  `teddycloud_nfc_context.md`), so a private repo is strongly
  recommended over a public one.
- **Backup check interval** — how often, in minutes, to re-check the
  repo (see below). Defaults to 10.

A `.nfc` sidecar upload also requires a configured `teddycloud-nfc-bridge`
sidecar URL (see above) — without one, matches are found but never
imported.

**Matching is by filename, not tag identity**: a raw `.nfc` dump carries
no title of its own — only a physical tag's identity, which is only
discoverable *after* it's uploaded. So name your backup files after the
Tonie itself (e.g. `Die Eiskönigin.nfc`); the integration compares each
not-yet-acquired wishlist item's title against the repo's filenames
case-insensitively, treating `-`/`_` as spaces (so `die-eiskoenigin.nfc`
or `Die_Eiskönigin_Teil2.nfc` both match a wishlist title of "Die
Eiskönigin"). On a match, the file is uploaded through the sidecar to
**every** box on the server — a physical tag's content isn't box-specific,
so there's no per-box choice to make.

This check runs automatically: once on Home Assistant startup, and
afterward at the configured interval (a GitHub directory listing on every
20-second box poll would be wasteful for a repo that changes rarely). For
an immediate check right after adding a wishlist item or a new backup
file, call `POST /api/teddycloud/wishlist/<device_id>/import_from_backups`.

## Standalone player page

Each Tonie Library entry also carries a `player_url`
(`/api/teddycloud/player/<entry_id>/<box>/<ruid>`) alongside `audio_url` — a
minimal, self-contained HTML page (cover art, title, chapter navigation
when a Tonie has more than one track, and enough JS to register the
[Media Session API](https://developer.mozilla.org/en-US/docs/Web/API/Media_Session_API)
for lock-screen controls) meant to be opened in its own browser tab. The
ha-teddycloud-card uses this rather than playing inline: two different
inline approaches were tried and both eventually got wiped out, together
with the whole player UI disappearing, a few minutes in — pointing at Home
Assistant's own frontend rebuilding the dashboard view (and every card in
it) after recovering from a websocket outage, unrelated to audio or
networking specifically. A standalone tab isn't part of that dashboard's
lifecycle at all, so rebuilding the dashboard can't touch it.

Playback tries three paths, in order, each falling back to the next if it
doesn't work out:

**1. Native streaming.** A plain `<audio>` `<source>` pointed straight at
the stream proxy above — no `fetch()`, no `Blob`, no `MediaSource` —
exactly the same approach teddyCloud's own web UI's player uses. The
proxy's Range support means the browser resolves duration and arbitrary
seeking itself, immediately; confirmed on real iOS hardware to also
survive the phone being locked/backgrounded during playback, same as any
other native `<audio>` element (podcast web players rely on the same
platform behavior). AirPlay needs no special handling either, since the
source is already a plain network URL, not a `blob:` one.

One quirk, confirmed on real iOS hardware: `audio.play()` can reject with
WebKit's `NotAllowedError` here specifically (duration/seeking still
resolve correctly either way) — the other two paths' source is a local
`blob:` URL (already-downloaded data) by the time `play()` runs, while
this one is a genuine network URL, and iOS Safari's autoplay-with-sound
policy doesn't extend the same allowance to that in a freshly
`window.open()`'d tab. Not treated as a failure: the page keeps the
native-stream source in place and shows a "tap ▶ to start" prompt instead
of discarding it and falling back to MediaSource, since a direct tap on
the visible play control is a fresh, in-document gesture that succeeds
immediately.

**2. MediaSource/WebM remux**, if native streaming's `<source>` fails to
load at all: `/api/teddycloud/remux/<entry_id>/<box>/<ruid>` remuxes (not
transcodes — `ffmpeg -c:a copy`, no re-encoding, requires `ffmpeg` —
bundled with Home Assistant OS and the official Container image, not
guaranteed elsewhere) teddyCloud's Ogg/Opus into WebM as it downloads
(sourced from the same local cache described above — instant if this
Tonie has already been streamed once, one download if not), and the
player page progressively appends it to a `MediaSource` (or, on iOS
Safari 17.1+, `ManagedMediaSource`). WebM, not MP4: confirmed on real
hardware that Safari's Opus support for MediaSource is for WebM
specifically, not MP4. Appending is throttled and chunked to avoid
`QuotaExceededError` — both a buffered-ahead cap and, since a *single*
`fetch()` read can itself return a many-megabyte chunk regardless of how
the read loop is paced, slicing every chunk into 64KB pieces before each
append. Since teddyCloud's API has no total-duration field at all (even
its own web UI only learns duration by measuring the browser's `<audio>`
element after the full stream loads), the reported duration grows to
match what's been appended so far rather than claiming to be a live
stream; seeking ahead of the download isn't possible, since the remux
always starts from the beginning.

**3. Full download**, the original, always-available fallback: downloads
the whole file into memory before starting playback, trading a wait up
front for playback that needs no network at all once started. A single
`blob:` URL alone would leave AirPlay to another device broken (a
receiver has to fetch the source itself, and a `blob:` URL has no network
address to fetch) — fixed using
[WebKit's own documented pattern](https://webkit.org/blog/15036/how-to-use-media-source-extensions-with-airplay/):
the `<audio>` element gets two `<source>` children, the local copy first
(what actually plays) and the plain stream-proxy URL second, purely as an
AirPlay fallback.

**Chapters, lock-screen seeking, and resuming.** teddyCloud's own
per-track start offsets (`getTagIndex`'s `trackSeconds`, computed from
the actual audio file, the same way teddyCloud's own C server does it
internally) show up, whenever a Tonie has more than one track, as:

- a prev/next chapter row on the page itself, plus a tappable list below
  it with every chapter — jump straight to any of them instead of
  skipping one at a time. Titled with the real track name when
  available (a *separate* source from the offsets — teddyCloud's
  community tonies.json catalog, which only covers recognized official
  Tonies — so a custom/ripped Tonie's chapters just show as "Chapter N"
  instead). Confirmed on a real device with a 13-chapter Tonie: real
  titles, correct highlighting, direct jumps.
- `previoustrack`/`nexttrack` `MediaSession` handlers are also
  registered, but confirmed on real iOS hardware to *not* surface as
  lock-screen buttons — Safari's Now Playing widget there only exposes
  play/pause and a fixed ±10s skip (see below), regardless of what a page
  registers or what `seekOffset` it requests. Chapter jumping is
  therefore an in-page-only feature on iOS for now; the handlers are left
  in for whatever platforms do support them.

±10s skip buttons are wired up on the lock screen regardless of chapter
count (registered as `seekbackward`/`seekforward` with a 15s offset, but
iOS Safari always shows and uses its own fixed 10s regardless). Playback
position is remembered per Tonie (`localStorage`) across separate visits
to the page, so reopening one you were partway through
picks up close to where you left off rather than restarting at 0.

Known limitation, an upstream teddyCloud bug (not something this
integration's proxy is exposed to, per the caching above, but worth
knowing about if you ever use teddyCloud's own player directly): seeking
within roughly the last `TONIE_HEADER_LENGTH` bytes of a *never-cached*
stream can return `MEDIA_ERR_DECODE` — see "Audio streaming proxy" above
for the root cause.

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
