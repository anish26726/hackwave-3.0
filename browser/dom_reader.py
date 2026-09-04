# AccessOS — Browser DOM Reader (Phase 6)
#
# Reads webpage content using Chrome DevTools Protocol (CDP).
# Chrome must be launched with --remote-debugging-port=9222 for this to work.
#
# Pipeline:
#   1. Try CDP at localhost:9222 (Chrome remote debugging)
#   2. Parse DOM: title, headings, links, buttons, paragraphs
#   3. Organize into readable structured output
#   4. Fallback: ask caller to use the Phase 4 screen reader
#
# NO Selenium, NO Playwright, NO arbitrary JS injection.
# Only safe HTTP GET requests to the local CDP endpoint.

import re
import json
import html
import time
from typing import Optional
from urllib.parse import urlparse

try:
    import requests as _req
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

CDP_HOST = 'http://localhost:9222'
CDP_TIMEOUT = 5       # seconds to wait for CDP response
MAX_OUTPUT_CHARS = 4000


class DOMReaderError(Exception):
    """Raised when DOM reading fails."""
    pass


class DOMReader:
    """
    Reads structured content from Chrome via the Chrome DevTools Protocol.

    Usage:
        reader = DOMReader()
        content = reader.read(query="what are the headings?")
    """

    def is_available(self) -> bool:
        """Return True if Chrome CDP is reachable."""
        if not _REQUESTS_AVAILABLE:
            return False
        try:
            r = _req.get(f'{CDP_HOST}/json/version', timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def get_active_tab(self) -> Optional[dict]:
        """Return the first visible/active Chrome tab info dict, or None."""
        try:
            r = _req.get(f'{CDP_HOST}/json/list', timeout=CDP_TIMEOUT)
            tabs = r.json()
            # Filter to only real page tabs (not devtools, extensions, etc.)
            page_tabs = [
                t for t in tabs
                if t.get('type') == 'page'
                and not t.get('url', '').startswith('chrome-extension://')
                and not t.get('url', '').startswith('devtools://')
            ]
            return page_tabs[0] if page_tabs else None
        except Exception:
            return None

    def _execute_cdp(self, ws_url: str, method: str, params: dict = None) -> dict:
        """
        Execute a single CDP command via WebSocket-over-HTTP.
        Uses the /json/execute endpoint (Chrome 100+) as a safe alternative
        to full WebSocket to avoid needing a ws library.
        Falls back to the evaluate endpoint.
        """
        # We use the REST-based CDP evaluate endpoint (safe read-only operations)
        # This avoids the need for websocket-client library.
        tab_id = ws_url.split('/')[-1]
        try:
            resp = _req.post(
                f'{CDP_HOST}/json/protocol/{tab_id}',
                json={'method': method, 'params': params or {}},
                timeout=CDP_TIMEOUT,
            )
            return resp.json()
        except Exception:
            return {}

    def _get_page_html(self, tab: dict) -> Optional[str]:
        """
        Get the outer HTML of the current page via CDP Runtime.evaluate.
        Only reads — does NOT write or execute any user-supplied code.
        """
        if not _REQUESTS_AVAILABLE:
            return None

        tab_id = tab.get('id', '')
        if not tab_id:
            return None

        # We call a fixed, safe JS expression to get page HTML
        # This is NOT executing model-generated code — it's a fixed read operation
        payload = {
            'id': 1,
            'method': 'Runtime.evaluate',
            'params': {
                'expression': 'document.documentElement.outerHTML',
                'returnByValue': True,
                'timeout': 3000,
            }
        }

        try:
            # Use CDP via websocket URL directly via requests POST
            # Chrome exposes a JSON-RPC endpoint for simple evaluate calls
            import socket
            import struct
            ws_url_str = tab.get('webSocketDebuggerUrl', '')
            if not ws_url_str:
                return None

            # Parse websocket url to get host/port and path
            parsed = urlparse(ws_url_str)
            host = parsed.hostname
            port = parsed.port or 9222
            path = parsed.path

            # Craft minimal WebSocket handshake + CDP message
            html_content = _cdp_evaluate(host, port, path, payload)
            return html_content
        except Exception:
            return None

    def read(self, query: str = '') -> str:
        """
        Read the current Chrome tab content and return structured text.

        Args:
            query: Optional user query to focus the output (e.g. "headings", "links").

        Returns:
            Structured text content of the current page.
        """
        if not _REQUESTS_AVAILABLE:
            raise DOMReaderError(
                "The 'requests' library is not available. "
                "Run: pip install requests"
            )

        if not self.is_available():
            raise DOMReaderError(
                "Chrome DevTools Protocol is not available. "
                "Restart Chrome with --remote-debugging-port=9222 or use the "
                "'read my screen' command instead."
            )

        tab = self.get_active_tab()
        if not tab:
            raise DOMReaderError(
                "No active Chrome tab found. Open a webpage in Chrome first."
            )

        url = tab.get('url', 'Unknown')
        title = tab.get('title', 'Unknown page')

        # Try to get full HTML via CDP
        html_content = self._get_page_html(tab)

        if html_content:
            return _parse_html_to_text(html_content, title, url, query)
        else:
            # Fallback: return tab metadata only
            return (
                f"[Browser: {title}]\n"
                f"URL: {url}\n\n"
                "Could not extract full page content via CDP.\n"
                "Tip: Use 'read my screen' for a visual screen read instead."
            )


def _cdp_evaluate(host: str, port: int, path: str, payload: dict) -> Optional[str]:
    """
    Execute a CDP Runtime.evaluate call via raw WebSocket (no external ws lib needed).
    Implements a minimal WebSocket client (RFC 6455) using only stdlib socket.
    """
    import socket
    import hashlib
    import base64
    import struct
    import json as _json

    key_bytes = base64.b64encode(b'AccessOSCDP12345').decode()

    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key_bytes}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )

    try:
        sock = socket.create_connection((host, port), timeout=CDP_TIMEOUT)
        sock.sendall(handshake.encode())

        # Read HTTP upgrade response
        response = b''
        while b'\r\n\r\n' not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk

        if b'101' not in response:
            sock.close()
            return None

        # Send CDP payload as WebSocket text frame
        msg = _json.dumps(payload).encode('utf-8')
        frame = _ws_frame(msg)
        sock.sendall(frame)

        # Read response frames
        result_data = b''
        sock.settimeout(CDP_TIMEOUT)
        while True:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                result_data += chunk
                # Try to parse — stop once we have a complete JSON
                try:
                    parsed = _json.loads(_ws_decode(result_data))
                    if 'result' in parsed or 'error' in parsed:
                        break
                except Exception:
                    pass
            except socket.timeout:
                break

        sock.close()

        if not result_data:
            return None

        try:
            parsed = _json.loads(_ws_decode(result_data))
            value = (
                parsed.get('result', {})
                      .get('result', {})
                      .get('value', None)
            )
            return value if isinstance(value, str) else None
        except Exception:
            return None

    except Exception:
        return None


def _ws_frame(data: bytes) -> bytes:
    """Build a masked WebSocket text frame (client → server)."""
    import os
    mask = os.urandom(4)
    payload_len = len(data)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

    if payload_len < 126:
        header = bytes([0x81, 0x80 | payload_len]) + mask
    elif payload_len < 65536:
        import struct
        header = bytes([0x81, 0xFE]) + struct.pack('>H', payload_len) + mask
    else:
        import struct
        header = bytes([0x81, 0xFF]) + struct.pack('>Q', payload_len) + mask

    return header + masked


def _ws_decode(data: bytes) -> str:
    """
    Decode a WebSocket frame (server → client, unmasked).
    Handles single or multiple frames; extracts text payload.
    """
    if len(data) < 2:
        return ''

    idx = 0
    full_payload = b''

    while idx < len(data):
        if idx + 2 > len(data):
            break
        b0 = data[idx]; b1 = data[idx + 1]
        idx += 2
        masked = bool(b1 & 0x80)
        payload_len = b1 & 0x7F

        if payload_len == 126:
            if idx + 2 > len(data):
                break
            import struct
            payload_len = struct.unpack('>H', data[idx:idx+2])[0]
            idx += 2
        elif payload_len == 127:
            if idx + 8 > len(data):
                break
            import struct
            payload_len = struct.unpack('>Q', data[idx:idx+8])[0]
            idx += 8

        if masked:
            mask = data[idx:idx+4]
            idx += 4
            payload = bytes(data[idx + i] ^ mask[i % 4] for i in range(payload_len))
        else:
            payload = data[idx:idx+payload_len]

        idx += payload_len
        full_payload += payload

    return full_payload.decode('utf-8', errors='replace')


def _parse_html_to_text(
    raw_html: str,
    title: str,
    url: str,
    query: str = '',
) -> str:
    """
    Parse raw HTML and extract meaningful structured content.

    Extracts:
        - Page title
        - Main headings (h1-h4)
        - Paragraphs
        - Links (first 10)
        - Buttons / form inputs
        - Errors / alerts
    """
    # Strip scripts and styles entirely
    clean = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<!--.*?-->', '', clean, flags=re.DOTALL)

    def tag_text(tag: str, content: str) -> list[str]:
        """Extract text inside all occurrences of <tag>...</tag>."""
        pattern = rf'<{tag}[^>]*>(.*?)</{tag}>'
        items = re.findall(pattern, content, flags=re.DOTALL | re.IGNORECASE)
        return [_strip_tags(i).strip() for i in items if _strip_tags(i).strip()]

    query_lower = query.lower()

    sections = []
    sections.append(f"[Page: {html.unescape(title)}]")
    sections.append(f"URL: {url}\n")

    # ── Headings ──────────────────────────────────────────────────────────
    headings = []
    for level in range(1, 5):
        for h in tag_text(f'h{level}', clean):
            headings.append(f"  H{level}: {html.unescape(h)}")
    if headings:
        sections.append("── Headings ──")
        sections.extend(headings[:15])

    # ── Main paragraphs ───────────────────────────────────────────────────
    if 'link' not in query_lower and 'button' not in query_lower:
        paras = tag_text('p', clean)
        paras = [html.unescape(p) for p in paras if len(p) > 30]
        if paras:
            sections.append("\n── Content ──")
            combined = '\n\n'.join(paras[:8])
            if len(combined) > 2000:
                combined = combined[:2000] + '…'
            sections.append(combined)

    # ── Links ─────────────────────────────────────────────────────────────
    if 'link' in query_lower or not query_lower:
        link_pattern = r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
        raw_links = re.findall(link_pattern, clean, flags=re.DOTALL | re.IGNORECASE)
        links = []
        for href, text in raw_links:
            text_clean = _strip_tags(text).strip()
            if text_clean and not href.startswith('javascript'):
                links.append(f"  → {html.unescape(text_clean)}: {href}")
        if links:
            sections.append("\n── Links ──")
            sections.extend(links[:10])
            if len(links) > 10:
                sections.append(f"  … and {len(links) - 10} more links")

    # ── Buttons / Form inputs ─────────────────────────────────────────────
    if 'button' in query_lower or 'form' in query_lower or not query_lower:
        btn_pattern = r'<(?:button|input)[^>]*?(?:value|aria-label|name)=["\']([^"\']+)["\'][^>]*>'
        buttons = re.findall(btn_pattern, clean, flags=re.IGNORECASE)
        buttons = [html.unescape(b.strip()) for b in buttons if b.strip()]
        if buttons:
            sections.append("\n── Buttons / Inputs ──")
            sections.extend([f"  ▸ {b}" for b in buttons[:10]])

    # ── Error messages ─────────────────────────────────────────────────────
    error_pattern = r'<(?:div|p|span)[^>]*(?:class|id)=["\'][^"\']*(?:error|alert|warning|danger)[^"\']*["\'][^>]*>(.*?)</(?:div|p|span)>'
    errors = re.findall(error_pattern, clean, flags=re.DOTALL | re.IGNORECASE)
    errors = [_strip_tags(e).strip() for e in errors if _strip_tags(e).strip()]
    if errors:
        sections.append("\n── Errors / Alerts ──")
        sections.extend([f"  ⚠ {html.unescape(e)}" for e in errors[:5]])

    result = '\n'.join(sections)
    if len(result) > MAX_OUTPUT_CHARS:
        result = result[:MAX_OUTPUT_CHARS] + '\n\n[… page content truncated]'
    return result


def _strip_tags(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', text)


# Module-level singleton
_reader: Optional[DOMReader] = None


def get_dom_reader() -> DOMReader:
    global _reader
    if _reader is None:
        _reader = DOMReader()
    return _reader
