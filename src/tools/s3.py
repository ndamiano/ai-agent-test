"""A minimal S3-compatible client: PUT, GET, HEAD against one bucket, SigV4 over `requests`.

A signing bug cannot silently succeed: the server recomputes the signature (a mismatch is a 403)
and the payload's sha256 rides x-amz-content-sha256, so the bytes are verified end to end. The
signing core is exposed (`sign_headers`) so the test suite can drive it with AWS's published
SigV4 example and assert the documented signature byte for byte.

Path-style addressing (https://endpoint/bucket/key) so a bucket needs no DNS of its own — Spaces,
B2 and AWS all accept it. Configuration is the `s3` block in settings.json; `configured()` is the
gate every caller checks, because archiving is a boundary like snapshots: a bucket that is not
there must never cost a build anything.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import quote

import requests

from config.settings_manager import settings_manager

logger = logging.getLogger(__name__)

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _cfg() -> Dict:
    return settings_manager.get_settings().get("s3") or {}


def configured() -> bool:
    c = _cfg()
    return all(c.get(k) for k in ("endpoint", "region", "bucket", "access_key", "secret_key"))


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_headers(*, method: str, host: str, uri: str, region: str, access_key: str,
                 secret_key: str, payload_hash: str, amz_date: str,
                 extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The SigV4 chain, verbatim from the spec: canonical request → string to sign → derived key.
    `uri` must already be URI-encoded the way it will be sent. Returns the headers to attach,
    Authorization included."""
    datestamp = amz_date[:8]
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date,
               **{k.lower(): v for k, v in (extra_headers or {}).items()}}
    signed_names = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers))
    canonical_request = "\n".join([method, uri, "", canonical_headers, signed_names, payload_hash])
    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode("utf-8"), datestamp),
                            region), "s3"), "aws4_request")
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out = {k: v for k, v in headers.items() if k != "host"}
    out["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                            f"SignedHeaders={signed_names}, Signature={signature}")
    return out


def _request(method: str, key: str, data: bytes = b"") -> requests.Response:
    c = _cfg()
    endpoint = c["endpoint"].replace("https://", "").replace("http://", "").rstrip("/")
    uri = "/" + quote(f"{c['bucket']}/{key}", safe="/-_.~")
    payload_hash = hashlib.sha256(data).hexdigest() if data else EMPTY_SHA256
    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    headers = sign_headers(method=method, host=endpoint, uri=uri, region=c["region"],
                           access_key=c["access_key"], secret_key=c["secret_key"],
                           payload_hash=payload_hash, amz_date=amz_date)
    return requests.request(method, f"https://{endpoint}{uri}", headers=headers,
                            data=data or None, timeout=300)


def put(key: str, data: bytes) -> None:
    res = _request("PUT", key, data)
    if res.status_code not in (200, 201):
        raise RuntimeError(f"s3 put {key}: {res.status_code} {res.text[:200]}")


def get(key: str) -> bytes:
    res = _request("GET", key)
    if res.status_code == 404:
        raise KeyError(key)
    if res.status_code != 200:
        raise RuntimeError(f"s3 get {key}: {res.status_code} {res.text[:200]}")
    return res.content


def head(key: str) -> Optional[int]:
    """The object's size, or None when it is not there — what an eviction checks before it
    deletes anything local."""
    res = _request("HEAD", key)
    if res.status_code == 404:
        return None
    if res.status_code != 200:
        raise RuntimeError(f"s3 head {key}: {res.status_code}")
    return int(res.headers.get("Content-Length", 0))
