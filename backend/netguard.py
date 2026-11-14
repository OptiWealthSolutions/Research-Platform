"""Outbound URL guard (SSRF) and blocked-site fetch fallback.

`pdf_url` / `source_url` come from third-party feeds. Before 2026-11-13 the
PDF proxy fetched them blindly, so a feed item pointing at
http://127.0.0.1:8000/... (the Horacle Hub) or 169.254.169.254 was fetched by
the server. `check_url` refuses non-http(s) schemes and any host that resolves
to a loopback, private, link-local, multicast or reserved address.
Redirects are followed manually and each hop is re-checked.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests


class UnsafeURL(ValueError):
    pass


def check_url(url: str) -> str:
    u = urlparse(url or "")
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UnsafeURL(f"scheme/host refused: {url!r}")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURL(f"DNS failure for {u.hostname}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise UnsafeURL(f"{u.hostname} resolves to non-public {ip}")
    return url


def safe_get(url: str, *, headers=None, timeout=30, stream=False, max_redirects=5):
    """GET with SSRF checks on every redirect hop."""
    cur = url
    for _ in range(max_redirects + 1):
        check_url(cur)
        r = requests.get(cur, headers=headers, timeout=timeout, stream=stream, allow_redirects=False)
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location")
            r.close()
            if not loc:
                break
            cur = urljoin(cur, loc)
            continue
        return r
    raise UnsafeURL(f"too many redirects from {url}")


def impersonated_get(url: str, *, headers=None, timeout=25):
    """Fallback for CDNs that 403 python-requests on its TLS fingerprint
    (RBA, BLS…). Returns a requests-like response or None if curl_cffi is absent."""
    try:
        from curl_cffi import requests as cr
    except Exception:
        return None
    h = {k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}
    return cr.get(url, headers=h, impersonate="chrome", timeout=timeout)
