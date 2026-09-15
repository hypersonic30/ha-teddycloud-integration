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
it. It's a cover image, an <audio> element played from a local blob: URL,
and enough JS to register Media Session metadata so the lock screen still
shows title/cover art and play/pause/seek controls.
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

        return web.Response(text=_render(title, picture, stream_url), content_type="text/html")


def _json_for_script(value) -> str:
    """json.dumps(), but safe to embed inside an inline <script> block.

    json.dumps() never escapes '<', so a title containing "</script>"
    would prematurely close the surrounding tag as far as the HTML parser
    is concerned — that runs *before* any JS parsing, so being "inside a
    JS string" doesn't protect against it. Escaping '<' as \\u003c (valid
    anywhere in a JS string) neutralizes that without changing the value.
    """
    return json.dumps(value).replace("<", "\\u003c")


def _render(title: str, picture: str | None, stream_url: str) -> str:
    safe_title = html.escape(title)
    safe_picture_attr = html.escape(picture) if picture else None
    js_title = _json_for_script(title)
    js_artwork = _json_for_script([{"src": picture}] if picture else [])
    js_stream_url = _json_for_script(stream_url)

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
</style>
</head>
<body>
  {cover_html}
  <h1>{safe_title}</h1>
  <div id="status">Loading…</div>
  <audio id="a" controls></audio>
  <script>
    // Downloads the whole file into memory before starting playback,
    // instead of streaming it live: once loaded, playback needs no network
    // at all, so nothing iOS does to a backgrounded tab's connections can
    // interrupt it. Streaming playback kept stopping in the background
    // even from this same minimal page — this trades a wait up front
    // (roughly the file size divided by your connection speed) for
    // eliminating the dependency on an open connection during playback.
    (async () => {{
      const statusEl = document.getElementById("status");
      const audio = document.getElementById("a");
      try {{
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
        audio.src = URL.createObjectURL(new Blob(chunks, {{ type: "audio/ogg" }}));
        statusEl.style.display = "none";
        audio.play();

        if ("mediaSession" in navigator) {{
          navigator.mediaSession.metadata = new MediaMetadata({{
            title: {js_title},
            artist: "TeddyCloud",
            artwork: {js_artwork},
          }});
          audio.addEventListener("play", () => {{ navigator.mediaSession.playbackState = "playing"; }});
          audio.addEventListener("pause", () => {{ navigator.mediaSession.playbackState = "paused"; }});
        }}
      }} catch (err) {{
        statusEl.textContent = "Failed to load: " + err.message;
      }}
    }})();
  </script>
</body>
</html>"""
