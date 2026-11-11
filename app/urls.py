import asyncio
import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 2048:
        raise ValueError("Enter a URL no longer than 2048 characters.")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS source URLs are supported.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("The URL must have a hostname and cannot contain credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The URL contains an invalid port.") from exc
    expected_port = 80 if parsed.scheme.lower() == "http" else 443
    if port not in {None, expected_port}:
        raise ValueError("Only the standard port for the selected HTTP scheme is allowed.")

    hostname = parsed.hostname.lower().rstrip(".")
    if not hostname or hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local hostnames are not allowed.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Non-public IP addresses are not allowed.")

    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        netloc = f"{hostname}:{port}"
    return urlunsplit(
        (parsed.scheme.lower(), netloc, parsed.path or "/", urlencode(query), "")
    )


async def ensure_public_host(url: str) -> None:
    hostname = urlsplit(url).hostname
    if not hostname:
        raise ValueError("Source URL has no hostname.")
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo, hostname, None, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise ValueError(f"Could not resolve source host {hostname}.") from exc
    addresses = {record[4][0] for record in records}
    if not addresses:
        raise ValueError(f"Source host {hostname} has no address records.")
    for raw_address in addresses:
        address = ipaddress.ip_address(raw_address.split("%", 1)[0])
        if not address.is_global:
            raise ValueError(f"Source host {hostname} resolves to a non-public address.")
