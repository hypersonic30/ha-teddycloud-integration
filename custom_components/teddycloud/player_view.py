"""HTTP view serving a minimal, standalone player page for one Tonie.

Meant to be opened in its own browser tab instead of playing inline in the
Home Assistant dashboard. A full HA dashboard is a heavy, actively
networking single-page app — Home Assistant's own websocket connection
gets suspended by iOS right alongside inline audio playback when a Safari
tab is backgrounded for a while (observed directly: both die at the same
moment). A bare, single-purpose page with nothing else going on is a much
better candidate for iOS to keep alive in the background, the same way a
podcast episode link reliably keeps playing when Safari is backgrounded.

This page is deliberately tiny: just a cover image, an <audio> element
pointed at the existing stream_view proxy, and enough JS to register
Media Session metadata so the lock screen still shows title/cover art and
play/pause/seek controls.
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
    safe_stream_url = html.escape(stream_url)
    js_title = _json_for_script(title)
    js_artwork = _json_for_script([{"src": picture}] if picture else [])

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
</style>
</head>
<body>
  {cover_html}
  <h1>{safe_title}</h1>
  <audio id="a" controls autoplay src="{safe_stream_url}"></audio>
  <script>
    if ("mediaSession" in navigator) {{
      navigator.mediaSession.metadata = new MediaMetadata({{
        title: {js_title},
        artist: "TeddyCloud",
        artwork: {js_artwork},
      }});
      const audio = document.getElementById("a");
      audio.addEventListener("play", () => {{ navigator.mediaSession.playbackState = "playing"; }});
      audio.addEventListener("pause", () => {{ navigator.mediaSession.playbackState = "paused"; }});
    }}
  </script>
</body>
</html>"""
