"""Pairing links and their QR codes.

The QR code on the Devices page holds `<portal URL>/pair?code=<code>`, an ordinary web address,
because every phone camera opens those. The Watchman app's own Scan button reads the same address
directly. In a browser, /pair shows the code and one button that opens the app link
`watchman://pair?server=<portal URL>&code=<code>`. Either way the app only fills in the form; the
person still taps Pair, so a stray QR code cannot pair a phone to someone else's server unnoticed.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

import segno
from fastapi import Request

from app.store.devices_store import PAIRING_ALPHABET


def normalize_code(text: str) -> str:
    """Accept a code typed any way: lower case, without dashes, with spaces. Returns the stored form
    (three groups of three joined by dashes), or the cleaned text when it is not nine characters."""
    chars = "".join(c for c in (text or "").upper() if c in PAIRING_ALPHABET)
    if len(chars) != 9:
        return chars
    return f"{chars[:3]}-{chars[3:6]}-{chars[6:]}"


def portal_url(request: Request) -> str:
    """The address devices should use, as the browser sees it. Behind `tailscale serve` the request
    reaches the server as plain HTTP on loopback, so the scheme comes from the forwarded header or,
    for a tailnet name, is HTTPS."""
    host = request.headers.get("host") or request.url.netloc
    proto = request.headers.get("x-forwarded-proto")
    if not proto:
        proto = "https" if host.split(":")[0].endswith(".ts.net") else request.url.scheme
    return f"{proto}://{host}"


def pair_link(server: str, code: str) -> str:
    """What the QR code holds: the portal's /pair page with the code."""
    return f"{server}/pair?" + urlencode({"code": code.replace("-", "")}, quote_via=quote)


def app_link(server: str, code: str) -> str:
    """What the /pair page's button opens: the app, with the form filled in."""
    return "watchman://pair?" + urlencode({"server": server, "code": code.replace("-", "")}, quote_via=quote)


def qr_svg(link: str) -> str:
    """An inline SVG of the link, ink on transparent, sized for a phone camera at arm's length."""
    return segno.make(link, error="m").svg_inline(scale=6, border=2, dark="#0b1114", light=None,
                                                  omitsize=False)
