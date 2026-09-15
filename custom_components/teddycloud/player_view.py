"""HTTP view serving a minimal, standalone player page for one Tonie.

Meant to be opened in its own browser tab instead of playing inline in the
Home Assistant dashboard. A full HA dashboard is a heavy, actively
networking single-page app — Home Assistant's own websocket connection
gets suspended by iOS right alongside inline audio playback when a Safari
tab is backgrounded for a while (observed directly: both die at the same
moment). A bare, single-purpose page with nothing else going on is a much
better candidate for iOS to keep alive in the background, the same way a
podcast episode link reliably keeps playing when Safari is backgrounded.

Being a bare page alone wasn't enough, though — playback still stopped in
the background sometimes, since a live stream still depends on an open
network connection iOS can suspend mid-playback. So this page downloads
the whole file into memory (via fetch()) before starting playback at all,
trading a wait up front for playback that needs no network whatsoever
once started — nothing iOS does to backgrounded connections can interrupt
it.

A single blob: URL alone would leave AirPlay to another device broken,
though: a receiver has to fetch the source itself, and a blob: URL has no
network address for it to fetch. WebKit has an official, documented
pattern for exactly this — see
https://webkit.org/blog/15036/how-to-use-media-source-extensions-with-airplay/
— give the <audio> element two <source> children instead of one: the
local blob: copy first (what actually plays normally), and the plain
stream_view proxy URL second, purely as an AirPlay fallback. Safari
transparently switches to the second source's URL when the user picks
AirPlay, handing the receiver something it can fetch on its own; normal
playback never touches that second source at all.

dev-branch experiment: playViaMSE() in the rendered page tries to start
playback almost immediately instead of waiting for the whole download, by
progressively appending a WebM remux (remux_view.py) to a
MediaSource as it downloads. Falls back to the proven playViaFullDownload()
above wherever MSE isn't usable (unsupported browser/codec, or any error
partway through), so this is additive, not a replacement.
"""
from __future__ import annotations

import html
import json

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_HEX_CHARS = set("0123456789abcdefABCDEF")


class TeddyCloudPlayerView(HomeAssistantView):
    """Minimal standalone player page for a single Tonie."""

    url = "/api/teddycloud/player/{entry_id}/{overlay}/{ruid}"
    name = "api:teddycloud:player"
    # HA's browser auth is token-based, attached by frontend JS to fetch/XHR
    # calls — a plain navigation like window.open() carries none of that,
    # same reasoning as TeddyCloudStreamView (which this page just embeds).
    requires_auth = False

    async def get(
        self, request: web.Request, entry_id: str, overlay: str, ruid: str
    ) -> web.Response:
        hass: HomeAssistant = request.app[KEY_HASS]
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            return web.Response(status=404)

        if len(ruid) != 16 or not all(c in _HEX_CHARS for c in ruid):
            return web.Response(status=400, text="Invalid ruid")

        box_data = coordinator.data.get(overlay) if coordinator.data else None
        tonie = None
        if box_data is not None:
            tonie = next((t for t in box_data.library if t["ruid"] == ruid), None)

        title = tonie["title"] if tonie else ruid
        picture = tonie.get("picture") if tonie else None
        stream_url = f"/api/teddycloud/stream/{entry_id}/{overlay}/{ruid}"
        remux_url = f"/api/teddycloud/remux/{entry_id}/{overlay}/{ruid}"

        return web.Response(
            text=_render(title, picture, stream_url, remux_url), content_type="text/html"
        )


def _json_for_script(value) -> str:
    """json.dumps(), but safe to embed inside an inline <script> block.

    json.dumps() never escapes '<', so a title containing "</script>"
    would prematurely close the surrounding tag as far as the HTML parser
    is concerned — that runs *before* any JS parsing, so being "inside a
    JS string" doesn't protect against it. Escaping '<' as \\u003c (valid
    anywhere in a JS string) neutralizes that without changing the value.
    """
    return json.dumps(value).replace("<", "\\u003c")


def _render(title: str, picture: str | None, stream_url: str, remux_url: str) -> str:
    safe_title = html.escape(title)
    safe_picture_attr = html.escape(picture) if picture else None
    js_title = _json_for_script(title)
    js_artwork = _json_for_script([{"src": picture}] if picture else [])
    js_stream_url = _json_for_script(stream_url)
    js_remux_url = _json_for_script(remux_url)

    cover_html = f'<img src="{safe_picture_attr}" alt="">' if safe_picture_attr else ""

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
  body {{
    margin: 0; min-height: 100vh; box-sizing: border-box; padding: 24px 16px;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 20px;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    background: #111; color: #eee; text-align: center;
  }}
  img {{ max-width: min(80vw, 320px); max-height: 45vh; border-radius: 12px; object-fit: contain; }}
  h1 {{ font-size: 1.1rem; font-weight: 500; margin: 0; word-break: break-word; }}
  audio {{ width: min(90vw, 400px); }}
  #status {{ font-size: 0.9rem; color: #aaa; }}
  #debug {{ font-size: 0.75rem; color: #777; max-width: 90vw; word-break: break-word; }}
</style>
</head>
<body>
  {cover_html}
  <h1>{safe_title}</h1>
  <div id="status">Loading…</div>
  <audio id="a" controls></audio>
  <div id="debug"></div>
  <script>
    const statusEl = document.getElementById("status");
    const audio = document.getElementById("a");
    // Stays visible permanently (unlike #status, which hides once playing)
    // so it's obvious which playback path actually ran without needing
    // Safari's remote Web Inspector - just look at the page.
    const debugEl = document.getElementById("debug");

    if ("mediaSession" in navigator) {{
      navigator.mediaSession.metadata = new MediaMetadata({{
        title: {js_title},
        artist: "TeddyCloud",
        artwork: {js_artwork},
      }});
      audio.addEventListener("play", () => {{ navigator.mediaSession.playbackState = "playing"; }});
      audio.addEventListener("pause", () => {{ navigator.mediaSession.playbackState = "paused"; }});
    }}

    // Experiment: let the browser stream straight off the network via a
    // plain <source>, the same way teddyCloud's own web UI's player
    // does - no fetch(), no Blob, no MediaSource, just native <audio>
    // playback against stream_view.py's Range-forwarding proxy. This is
    // the simplest possible approach and - if it holds up - gets
    // everything the other two paths have to work around: instant start
    // (the browser only needs the first bit to begin playing), correct
    // duration and free seeking from the very first frame (the browser
    // resolves both itself via Range requests, exactly like teddyCloud's
    // own player), and normal AirPlay (a plain network URL, not a blob:
    // one, so no dual-<source> trick needed either).
    //
    // What's unverified: whether this survives iOS backgrounding/lock
    // screen. The earlier finding that a live network connection gets
    // suspended by iOS was made against the HA dashboard's *inline*
    // player, where a live HA websocket died at the same moment - it may
    // have been that websocket/page script activity iOS was suspending,
    // not the native <audio> element's own network fetching specifically
    // (iOS has a sanctioned, exempted "background audio playback" mode
    // for exactly this, which is *why* podcast web players keep working
    // backgrounded). If that's the real explanation, this path should
    // survive backgrounding fine despite being "live" network audio,
    // and the fetch()-driven paths below it (which are ordinary page
    // script activity, not the sanctioned media-playback exemption)
    // would actually be *more* exposed to suspension, not less. Only a
    // real-device test settles this either way.
    async function playViaNativeStream() {{
      const source = document.createElement("source");
      source.type = "audio/ogg";
      source.src = {js_stream_url};
      audio.append(source);

      // A failed <source> fires "error" on *itself*, not on the parent
      // <audio> - confirmed with a real Chromium instance: an <audio>
      // with a single 404ing <source> child never fires its own "error"
      // event at all (only "emptied"), so listening there hangs forever
      // instead of ever rejecting. The <source> element is where the
      // real signal shows up.
      const ready = new Promise((resolve, reject) => {{
        audio.addEventListener("loadedmetadata", resolve, {{ once: true }});
        source.addEventListener(
          "error",
          () => reject(new Error("source failed to load")),
          {{ once: true }}
        );
      }});
      audio.load();
      await ready;
      statusEl.style.display = "none";

      try {{
        await audio.play();
      }} catch (playErr) {{
        if (playErr.name === "NotAllowedError") {{
          // Real audio, correct duration, and free seeking are all
          // already working at this point - confirmed on real iOS
          // hardware: only the *autoplay* itself gets blocked here,
          // consistent with the other two paths' <audio> source being a
          // local blob: URL (already-downloaded data) by the time
          // play() runs, while this one is a genuine network URL - iOS
          // Safari's autoplay-with-sound policy allows the former in a
          // freshly window.open()'d tab but not the latter. Not a real
          // failure of this path: leave the source in place with its
          // native controls visible rather than discarding all of that
          // and falling back to MSE/full-download. The user's own tap on
          // the visible play button supplies a fresh, in-document
          // gesture, which is unambiguously valid.
          statusEl.style.display = "";
          statusEl.textContent = "Tap ▶ to start playback";
          return "needs-tap";
        }}
        throw playErr;
      }}
      return "playing";
    }}

    // Proven, always-available path (was the only path before this dev
    // branch): download the whole file into memory before starting
    // playback at all. Once loaded, playback needs no network whatsoever,
    // so nothing iOS does to a backgrounded tab's connections can
    // interrupt it. The <audio> element gets two <source> children rather
    // than a plain .src: the local blob: copy first (what actually
    // plays), and the plain stream_view proxy URL second, purely so
    // AirPlay has something fetchable to hand a receiver (a blob: URL has
    // no network address for one to fetch) — see
    // https://webkit.org/blog/15036/how-to-use-media-source-extensions-with-airplay/.
    // Normal playback never touches that second source.
    async function playViaFullDownload(resumeFrom) {{
      const resp = await fetch({js_stream_url});
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const total = Number(resp.headers.get("Content-Length")) || 0;
      const reader = resp.body.getReader();
      const chunks = [];
      let received = 0;
      while (true) {{
        const {{ done, value }} = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.length;
        const mb = (received / 1048576).toFixed(1);
        statusEl.textContent = total
          ? `Loading… ${{Math.round((received / total) * 100)}}% (${{mb}} MB)`
          : `Loading… ${{mb}} MB`;
      }}

      audio.textContent = "";
      const localSource = document.createElement("source");
      localSource.src = URL.createObjectURL(new Blob(chunks, {{ type: "audio/ogg" }}));
      localSource.type = "audio/ogg";
      const airplaySource = document.createElement("source");
      airplaySource.src = {js_stream_url};
      airplaySource.type = "audio/ogg";

      // If this runs as a mid-playback fallback (the instant-start attempt
      // got partway through before failing), pick up where that left off
      // instead of restarting at 0.
      if (resumeFrom) {{
        const onResume = () => {{
          audio.removeEventListener("loadedmetadata", onResume);
          audio.currentTime = resumeFrom;
          audio.play();
        }};
        audio.addEventListener("loadedmetadata", onResume);
      }}

      audio.append(localSource, airplaySource);
      audio.load();

      statusEl.style.display = "none";
      if (!resumeFrom) audio.play();
    }}

    // Experimental (dev branch): start playback almost immediately by
    // progressively appending a WebM remux (see remux_view.py — same
    // audio, repackaged, not re-encoded) to a MediaSource-backed <source>
    // as it downloads, instead of waiting for the whole file. iOS Safari
    // needs the "Managed" variant of this API (17.1+); where neither is
    // available, or the browser can't decode this exact codec/container
    // combination, playViaFullDownload() above is used instead — as it
    // also is if anything here throws partway through.
    //
    // WebM, not MP4: confirmed on real hardware that Safari's Opus
    // support for MediaSource is for WebM specifically — see
    // remux_view.py's docstring for how that was verified.
    async function playViaMSE(MSClass) {{
      const mimeType = 'audio/webm; codecs="opus"';
      const mediaSource = new MSClass();

      const localSource = document.createElement("source");
      localSource.type = "audio/webm";
      localSource.src = URL.createObjectURL(mediaSource);
      const airplaySource = document.createElement("source");
      airplaySource.type = "audio/ogg";
      airplaySource.src = {js_stream_url};
      audio.append(localSource, airplaySource);

      const openPromise = new Promise((resolve, reject) => {{
        mediaSource.addEventListener("sourceopen", resolve, {{ once: true }});
        mediaSource.addEventListener("error", () => reject(new Error("MediaSource error")), {{
          once: true,
        }});
      }});
      audio.load();
      await openPromise;

      const sourceBuffer = mediaSource.addSourceBuffer(mimeType);

      // Downloading is many times faster than playback consumes it (a
      // multi-hour recording can fully download in well under a minute),
      // so appending as fast as it arrives quickly overruns the
      // SourceBuffer's memory quota (a real QuotaExceededError, not
      // theoretical - hit this on real hardware, twice: once with no
      // throttling at all, and again with a 60-second-ahead cap that was
      // still too generous for whatever ManagedMediaSource actually
      // allows on iOS). The failure diagnostics from that second attempt
      // (bufferedAhead=4.9s, chunk=18523602B) proved throttling *between*
      // reads can't be the whole story: fetch()'s reader.read() itself
      // handed back an ~18.5MB chunk in one call - the network layer
      // buffers under the hood regardless of how slowly this loop
      // consumes it, so a single appendBuffer() call can still blow the
      // quota no matter how well-timed the reads are. Fixed by capping
      // the size of any *individual* appendBuffer() call, slicing each
      // read() result into pieces and re-checking buffer space between
      // slices, not just between reads.
      const MAX_BUFFER_AHEAD_SECONDS = 5;
      const MAX_APPEND_BYTES = 65536;
      let totalAppended = 0;
      const bufferedAheadSeconds = () => {{
        if (sourceBuffer.buffered.length === 0) return 0;
        const end = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1);
        return end - audio.currentTime;
      }};
      const waitForBufferSpace = () =>
        new Promise((resolve) => {{
          const check = () => {{
            if (bufferedAheadSeconds() <= MAX_BUFFER_AHEAD_SECONDS) resolve();
            else setTimeout(check, 250);
          }};
          check();
        }});
      const appendChunk = (chunk) =>
        new Promise((resolve, reject) => {{
          const fail = (label) => {{
            reject(
              new Error(
                `${{label}} (bufferedAhead=${{bufferedAheadSeconds().toFixed(1)}}s, ` +
                  `chunk=${{chunk.length}}B, totalAppended=${{(totalAppended / 1024).toFixed(0)}}KB)`
              )
            );
          }};
          sourceBuffer.addEventListener(
            "updateend",
            () => {{
              totalAppended += chunk.length;
              resolve();
            }},
            {{ once: true }}
          );
          sourceBuffer.addEventListener("error", () => fail("SourceBuffer error event"), {{
            once: true,
          }});
          try {{
            sourceBuffer.appendBuffer(chunk);
          }} catch (err) {{
            fail(`${{err.name}}: ${{err.message}}`);
          }}
        }});

      // teddyCloud's API has no total-duration field at all - confirmed
      // against its own source: even teddyCloud's own official web UI
      // only learns duration by measuring the browser's <audio> element
      // after the full stream has loaded, exactly like this page's
      // playViaFullDownload() does. A MediaSource with no duration ever
      // set reads as an unbounded live stream to Safari for as long as
      // that lasts - no seek bar, no time display, exactly what shows up
      // here while the background download is still running. Growing
      // mediaSource.duration to match what's actually been appended so
      // far (never shrinking it, which would truncate already-buffered
      // media - only ever increasing) fixes that without needing to know
      // the real total ahead of time: the seek bar reaches exactly as
      // far as the download has progressed, landing on the exact total
      // once it finishes. Seeking ahead of that isn't possible either
      // way - the remux always starts from the beginning of the stream,
      // so there's no byte offset to jump to for audio that hasn't
      // downloaded yet.
      const growDuration = () => {{
        if (mediaSource.readyState !== "open" || sourceBuffer.updating) return;
        if (sourceBuffer.buffered.length === 0) return;
        const end = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1);
        if (Number.isNaN(mediaSource.duration) || end > mediaSource.duration) {{
          mediaSource.duration = end;
        }}
      }};

      const resp = await fetch({js_remux_url});
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const reader = resp.body.getReader();

      let started = false;
      while (true) {{
        const {{ done, value }} = await reader.read();
        if (done) break;
        // Slice unconditionally, even when value is already small: it
        // keeps every appendBuffer() call the same predictable size
        // regardless of how the network/fetch layer happened to batch
        // this particular read().
        for (let offset = 0; offset < value.length; offset += MAX_APPEND_BYTES) {{
          const slice = value.subarray(offset, offset + MAX_APPEND_BYTES);
          await waitForBufferSpace();
          await appendChunk(slice);
          growDuration();
          if (!started) {{
            started = true;
            statusEl.style.display = "none";
            audio.play();
          }}
        }}
      }}
      if (mediaSource.readyState === "open") mediaSource.endOfStream();
    }}

    (async () => {{
      const MSClass = window.ManagedMediaSource || window.MediaSource;
      const which = window.ManagedMediaSource
        ? "ManagedMediaSource"
        : window.MediaSource
          ? "MediaSource"
          : "none";

      // Diagnostic pass: MP4+Opus is what playViaMSE() actually uses right
      // now, but WebKit's Opus additions (Safari 18.4) were specifically
      // for WebM, not MP4 — checking several combinations at once here so
      // a single report settles which container/codec string (if any)
      // this device actually supports, instead of another guess-and-check
      // round trip.
      const candidates = [
        'audio/mp4; codecs="opus"',
        'audio/webm; codecs="opus"',
        'audio/webm;codecs=opus',
        'audio/ogg; codecs="opus"',
      ];
      const support = MSClass
        ? candidates.map((c) => `${{c}} → ${{MSClass.isTypeSupported(c)}}`).join(" | ")
        : "(no MediaSource class at all)";

      const canTryMSE =
        !!MSClass &&
        typeof MSClass.isTypeSupported === "function" &&
        MSClass.isTypeSupported('audio/webm; codecs="opus"');
      debugEl.textContent = `MSE class: ${{which}} — ${{support}}`;

      // Tried first, ahead of everything else below: see
      // playViaNativeStream()'s comment for why this might turn out to
      // be strictly better than either fallback if it survives
      // backgrounding - instant start, correct duration, and free
      // seeking all for free, no ffmpeg/MSE involved. Returns
      // immediately on success so only one path is ever actually
      // exercised at a time.
      try {{
        debugEl.textContent += " — trying native stream…";
        const outcome = await playViaNativeStream();
        debugEl.textContent +=
          outcome === "needs-tap"
            ? " — native stream ready (experiment), tap ▶ to start"
            : " — playing via native stream (experiment)";
        return;
      }} catch (nativeErr) {{
        debugEl.textContent += ` — native stream failed (${{nativeErr.message}})`;
        audio.textContent = "";
      }}

      try {{
        if (canTryMSE) {{
          debugEl.textContent += " — trying instant-start…";
          await playViaMSE(MSClass);
          debugEl.textContent += " — playing via instant-start (MSE)";
        }} else {{
          debugEl.textContent += " — playing via full download";
          await playViaFullDownload();
        }}
      }} catch (err) {{
        if (canTryMSE) {{
          debugEl.textContent += ` — instant-start failed (${{err.message}}), falling back`;
          try {{
            // Resume from wherever instant-start got to instead of
            // restarting at 0, in case it was already playing when it
            // failed (e.g. a QuotaExceededError partway through).
            await playViaFullDownload(audio.currentTime || undefined);
            debugEl.textContent += " — playing via full download";
            return;
          }} catch (fallbackErr) {{
            statusEl.textContent = "Failed to load: " + fallbackErr.message;
            return;
          }}
        }}
        statusEl.textContent = "Failed to load: " + err.message;
      }}
    }})();
  </script>
</body>
</html>"""
