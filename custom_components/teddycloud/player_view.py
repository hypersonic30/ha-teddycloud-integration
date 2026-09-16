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

The <audio> element has preload="auto": a real-device report of audible
stuttering (while audio.currentTime kept advancing normally - not the
seeking/caching issue found and fixed earlier, something else) turned
out to be over a cellular+VPN connection rather than home WiFi, not yet
confirmed whether it reproduces on WiFi at all. preload="auto" is a
low-risk hint encouraging the browser to buffer further ahead than it
otherwise would, worth having regardless of what that comparison shows -
unconfirmed as an actual fix for what was reported.
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
        chapters = tonie.get("chapters") if tonie else []
        stream_url = f"/api/teddycloud/stream/{entry_id}/{overlay}/{ruid}"
        remux_url = f"/api/teddycloud/remux/{entry_id}/{overlay}/{ruid}"

        return web.Response(
            text=_render(title, picture, stream_url, remux_url, ruid, chapters),
            content_type="text/html",
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


def _render(
    title: str,
    picture: str | None,
    stream_url: str,
    remux_url: str,
    ruid: str,
    chapters: list[dict] | None = None,
) -> str:
    safe_title = html.escape(title)
    safe_picture_attr = html.escape(picture) if picture else None
    js_title = _json_for_script(title)
    js_artwork = _json_for_script([{"src": picture}] if picture else [])
    js_stream_url = _json_for_script(stream_url)
    js_remux_url = _json_for_script(remux_url)
    js_ruid = _json_for_script(ruid)
    js_chapters = _json_for_script(sorted(chapters or [], key=lambda c: c["start"]))

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
  #chapters {{ display: flex; align-items: center; gap: 16px; }}
  #chapters button {{
    background: #222; color: #eee; border: 1px solid #444; border-radius: 8px;
    font-size: 1.1rem; padding: 6px 14px; cursor: pointer;
  }}
  #chapterLabel {{ font-size: 0.85rem; color: #aaa; min-width: 5em; }}
  #chapterList {{
    display: flex; flex-direction: column; gap: 4px; width: min(90vw, 400px);
    max-height: 40vh; overflow-y: auto;
  }}
  #chapterList button {{
    background: #1a1a1a; color: #eee; border: 1px solid #333; border-radius: 8px;
    font-size: 0.9rem; padding: 8px 12px; cursor: pointer; text-align: left;
  }}
  #chapterList button.current {{ background: #2a4; border-color: #2a4; font-weight: 600; }}
</style>
</head>
<body>
  {cover_html}
  <h1>{safe_title}</h1>
  <div id="status">Loading…</div>
  <audio id="a" controls preload="auto"></audio>
  <div id="chapters" hidden>
    <button id="prevChapter" type="button">⏮</button>
    <span id="chapterLabel"></span>
    <button id="nextChapter" type="button">⏭</button>
  </div>
  <div id="chapterList" hidden></div>
  <div id="debug"></div>
  <script>
    const statusEl = document.getElementById("status");
    const audio = document.getElementById("a");
    // Stays visible permanently (unlike #status, which hides once playing)
    // so it's obvious which playback path actually ran without needing
    // Safari's remote Web Inspector - just look at the page.
    const debugEl = document.getElementById("debug");

    // Per-track start offsets (seconds) plus, when available, a real
    // title - both straight from teddyCloud's own getTagIndex response,
    // no extra parsing needed here. Offsets come from parsing the actual
    // audio file, so they're always present for a multi-track Tonie;
    // titles come from a *separate* source (teddyCloud's community
    // tonies.json catalog, only for recognized official Tonies) and can
    // be missing (null) for some or all chapters - falls back to a plain
    // "Chapter N" label in that case. Empty array for a single-track Tonie.
    const CHAPTERS = {js_chapters};

    function currentChapterIndex() {{
      let idx = 0;
      for (let i = 0; i < CHAPTERS.length; i++) {{
        if (CHAPTERS[i].start <= audio.currentTime + 0.5) idx = i;
        else break;
      }}
      return idx;
    }}
    function seekToChapter(idx) {{
      if (idx < 0 || idx >= CHAPTERS.length) return;
      audio.currentTime = CHAPTERS[idx].start;
    }}
    function prevChapter() {{
      const idx = currentChapterIndex();
      // More than 3s into the current chapter: restart it instead of
      // jumping to the previous one, matching how "previous track"
      // behaves on most players.
      seekToChapter(audio.currentTime - CHAPTERS[idx].start > 3 ? idx : idx - 1);
    }}
    function nextChapter() {{
      seekToChapter(currentChapterIndex() + 1);
    }}
    function chapterLabelText(i) {{
      return CHAPTERS[i].title || `Chapter ${{i + 1}}`;
    }}

    if (CHAPTERS.length > 1) {{
      const chaptersEl = document.getElementById("chapters");
      chaptersEl.hidden = false;
      document.getElementById("prevChapter").addEventListener("click", prevChapter);
      document.getElementById("nextChapter").addEventListener("click", nextChapter);
      const chapterLabel = document.getElementById("chapterLabel");

      // Full tappable list, so a chapter far ahead is one tap away
      // instead of repeated skipping with prev/next.
      const listEl = document.getElementById("chapterList");
      listEl.hidden = false;
      const listButtons = CHAPTERS.map((chapter, i) => {{
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = chapterLabelText(i);
        btn.addEventListener("click", () => seekToChapter(i));
        listEl.append(btn);
        return btn;
      }});

      let lastHighlighted = -1;
      audio.addEventListener("timeupdate", () => {{
        const idx = currentChapterIndex();
        chapterLabel.textContent = `${{idx + 1}} / ${{CHAPTERS.length}}`;
        if (idx !== lastHighlighted) {{
          if (lastHighlighted >= 0) listButtons[lastHighlighted].classList.remove("current");
          listButtons[idx].classList.add("current");
          lastHighlighted = idx;
        }}
      }});
    }}

    if ("mediaSession" in navigator) {{
      navigator.mediaSession.metadata = new MediaMetadata({{
        title: {js_title},
        artist: "TeddyCloud",
        artwork: {js_artwork},
      }});
      audio.addEventListener("play", () => {{ navigator.mediaSession.playbackState = "playing"; }});
      audio.addEventListener("pause", () => {{ navigator.mediaSession.playbackState = "paused"; }});
      // Lock-screen skip buttons - confirmed on real iOS hardware that
      // Safari's Now Playing widget only ever shows these two as a fixed
      // ±10s regardless of the seekOffset requested here.
      navigator.mediaSession.setActionHandler("seekbackward", (details) => {{
        audio.currentTime = Math.max(0, audio.currentTime - (details.seekOffset || 15));
      }});
      navigator.mediaSession.setActionHandler("seekforward", (details) => {{
        audio.currentTime = Math.min(audio.duration || Infinity, audio.currentTime + (details.seekOffset || 15));
      }});
      // Registered for whatever platforms do support them, but confirmed
      // on real iOS hardware that Safari's lock-screen widget does *not*
      // surface previoustrack/nexttrack as buttons for web audio at all -
      // chapter jumping stays an in-page-only feature there.
      if (CHAPTERS.length > 1) {{
        navigator.mediaSession.setActionHandler("previoustrack", prevChapter);
        navigator.mediaSession.setActionHandler("nexttrack", nextChapter);
      }}
    }}

    // Resume where playback left off last time, across separate visits to
    // this page (a closed tab, a reloaded phone) - plain localStorage,
    // keyed by ruid. Only applied once per page load, and only if the
    // saved position is actually reachable yet (audio.seekable) - the MSE
    // fallback path in particular can't seek ahead of what it has
    // buffered so far, so this safely does nothing there rather than
    // stalling on an unreachable seek.
    const RESUME_KEY = "teddycloud_resume_" + {js_ruid};
    let resumeApplied = false;
    let lastSavedPosition = -1;
    audio.addEventListener("loadedmetadata", () => {{
      if (resumeApplied) return;
      resumeApplied = true;
      try {{
        const saved = parseFloat(localStorage.getItem(RESUME_KEY));
        if (!(saved > 5)) return;
        if (audio.duration && saved > audio.duration - 5) return;
        const seekable = audio.seekable;
        const reachable = seekable.length > 0 && saved <= seekable.end(seekable.length - 1);
        if (reachable) audio.currentTime = saved;
      }} catch (err) {{
        // localStorage/seekable can throw (private window, blocked
        // storage) - starting from 0 is a perfectly safe fallback.
      }}
    }});
    audio.addEventListener("timeupdate", () => {{
      if (Math.abs(audio.currentTime - lastSavedPosition) < 5) return;
      lastSavedPosition = audio.currentTime;
      try {{
        localStorage.setItem(RESUME_KEY, String(audio.currentTime));
      }} catch (err) {{
        // ignore - see above
      }}
    }});
    audio.addEventListener("ended", () => {{
      try {{
        localStorage.removeItem(RESUME_KEY);
      }} catch (err) {{
        // ignore - see above
      }}
    }});

    // Persistent diagnostics for whatever happens *after* a playback path
    // has already succeeded - e.g. a later seek failing. None of the
    // per-path error handling below covers this (it only watches the
    // initial load), so without this, a later failure only ever shows up
    // as Safari's own generic native-controls "Fehler" text, with no
    // code or context to go on. Unlike the initial-load case (where a
    // failed <source> fires "error" on itself, confirmed via a real
    // Chromium probe), a resource that already loaded successfully and
    // then hits a genuine network/decode problem fires "error" on the
    // <audio> element itself per spec, so that's what this listens to.
    const MEDIA_ERROR_NAMES = {{
      1: "MEDIA_ERR_ABORTED",
      2: "MEDIA_ERR_NETWORK",
      3: "MEDIA_ERR_DECODE",
      4: "MEDIA_ERR_SRC_NOT_SUPPORTED",
    }};
    // Wall-clock time alongside audio.currentTime specifically: a real
    // device report showed audio.currentTime jumping from ~1425s back to
    // ~2.9s mid-session with no page reload (the debug line's own
    // history proved that - a reload would have reset it to empty) and
    // no way to tell from currentTime alone whether that was instant
    // (a real reset) or happened after a long gap (e.g. the tab being
    // backgrounded for a while) - only a real wall-clock delta between
    // log lines can distinguish those. document.hidden is logged for the
    // same reason: was the tab actually backgrounded at the time.
    const logEvent = (label) => {{
      // Local wall-clock time, not toISOString()'s UTC - this is read
      // against the user's own clock while debugging a real session, and
      // toISOString() being 1-2 hours off from what's on screen (DST-
      // dependent) made that comparison wrong outside UTC.
      const wallClock = new Date().toLocaleTimeString("en-GB", {{ hour12: false }});
      debugEl.textContent +=
        ` — [${{wallClock}}] ${{label}} at t=${{audio.currentTime.toFixed(1)}}s` +
        ` (readyState=${{audio.readyState}}, networkState=${{audio.networkState}}, hidden=${{document.hidden}})`;
    }};
    audio.addEventListener("error", () => {{
      const err = audio.error;
      logEvent(
        `ERROR: ${{err ? MEDIA_ERROR_NAMES[err.code] || `code ${{err.code}}` : "unknown"}}` +
          (err && err.message ? ` (${{err.message}})` : "")
      );
    }});
    // "stalled"/"waiting"/"suspend" can be entirely normal (a momentary
    // buffering hiccup the browser recovers from on its own) - logged
    // for context around a real failure, not because each one alone
    // means something broke. "seeking"/"seeked"/"pause" without the user
    // having touched anything would point at something (our own code, or
    // the browser itself) changing position/state unexpectedly.
    // "loadedmetadata" firing more than once would mean the browser
    // re-initialized the whole media resource from scratch mid-session -
    // exactly the kind of event that could explain currentTime jumping
    // back unexpectedly with no page reload involved.
    ["stalled", "waiting", "suspend", "emptied", "seeking", "seeked", "pause", "loadedmetadata"].forEach((evt) => {{
      audio.addEventListener(evt, () => logEvent(evt));
    }});
    document.addEventListener("visibilitychange", () => {{
      logEvent(`visibilitychange`);
    }});

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
