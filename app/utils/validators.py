"""URL validation + SSRF protection.

Layers:
  1. `normalize_url`  - syntactic checks (scheme, creds, port, FQDN).
  2. `ensure_public_host` - resolves the host and rejects ANY non-global
     address (loopback, RFC1918, link-local/cloud metadata 169.254.169.254,
     CGNAT, ULA, multicast, reserved, IPv4-mapped IPv6 of those, ...).
The extractor calls (2) before every hop, including each redirect.

Known limitation: a DNS-rebinding attacker could change the answer between our
check and httpx's own lookup. Mitigate in production with an egress firewall /
dedicated network namespace that blocks private ranges (see README).
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from app.errors import BlockedAddressError, InvalidURLError, WebsiteUnreachableError

ALLOWED_PORTS = {None, 80, 443}
MAX_URL_LENGTH = 2048
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet", ".localdomain")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def normalize_url(raw: str) -> str:
    """Return a canonical http(s) URL or raise InvalidURLError."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidURLError("URL must be a non-empty string.")
    raw = raw.strip()
    if len(raw) > MAX_URL_LENGTH:
        raise InvalidURLError("URL is too long.")
    if any(c.isspace() or ord(c) < 32 for c in raw):
        raise InvalidURLError("URL must not contain whitespace or control characters.")
    if "://" not in raw:
        raw = "https://" + raw  # allow "example.com"

    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise InvalidURLError("URL is malformed.") from None

    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidURLError("Only http:// and https:// URLs are supported.")
    if parts.username is not None or parts.password is not None:
        raise InvalidURLError("URLs containing credentials are not allowed.")
    if port not in ALLOWED_PORTS:
        raise InvalidURLError("Only the default web ports (80/443) are allowed.")
    if not parts.hostname:
        raise InvalidURLError("URL has no hostname.")

    host = parts.hostname.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")  # punycode for IDNs
    except UnicodeError:
        raise InvalidURLError("Hostname is not valid.") from None

    if not _is_ip_literal(host):
        # Single-label names (localhost, intranet) and decimal-IP tricks such
        # as http://2130706433 have no dot -> rejected here.
        if "." not in host:
            raise InvalidURLError("Hostname must be a fully-qualified domain name.")
        if host.endswith(BLOCKED_SUFFIXES):
            raise BlockedAddressError("Internal hostnames are not allowed.")

    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 must be judged as 127.0.0.1
    return ip.is_global and not ip.is_multicast


async def ensure_public_host(host: str, port: int | None = None) -> None:
    """Resolve `host` and raise unless EVERY resolved address is public."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror:
        raise WebsiteUnreachableError(f"DNS lookup failed for '{host}'.") from None
    if not infos:
        raise WebsiteUnreachableError(f"No address found for '{host}'.")
    for info in infos:
        addr = info[4][0].split("%")[0]  # strip IPv6 zone id
        if not is_public_ip(ipaddress.ip_address(addr)):
            raise BlockedAddressError("The URL resolves to a private or reserved network address.")


async def validate_url(raw: str) -> str:
    """Full validation: syntax + DNS/SSRF check. Returns the normalized URL."""
    url = normalize_url(raw)
    parts = urlsplit(url)
    await ensure_public_host(parts.hostname, parts.port)
    return url
