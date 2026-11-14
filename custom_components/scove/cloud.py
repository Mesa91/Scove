"""Client minimo del cloud iRobot: login, mappe (pmaps) e geometria (UMF).

Serve per tre cose che il robot non dà in locale: la password locale,
l'elenco delle stanze con la versione della mappa e i poligoni di stanze
e zone vietate. Tutto il resto passa dalla connessione locale.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import time
import uuid
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

DISCOVERY_URL = "https://disc-prod.iot.irobotapi.com/v1/discover/endpoints"
TIMEOUT = aiohttp.ClientTimeout(total=30)


class CloudError(Exception):
    """Errore generico del cloud."""


class CloudAuthError(CloudError):
    """Credenziali rifiutate."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign_request(
    method: str, url: str, region: str, creds: dict[str, str],
    now: dt.datetime | None = None,
) -> dict[str, str]:
    """Firma AWS Signature V4 per il servizio execute-api (solo GET senza corpo)."""
    now = now or dt.datetime.now(dt.UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    parts = urlsplit(url)
    path = quote(parts.path or "/", safe="/-_.~")
    pairs = sorted(p.split("=", 1) if "=" in p else [p, ""] for p in parts.query.split("&") if p)
    query = "&".join(f"{k}={v}" for k, v in pairs)
    token = creds["SessionToken"]
    headers = f"host:{parts.netloc}\nx-amz-date:{amz_date}\nx-amz-security-token:{token}\n"
    signed = "host;x-amz-date;x-amz-security-token"
    canonical = "\n".join([method, path, query, headers, signed, _sha(b"")])
    scope = f"{date}/{region}/execute-api/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, _sha(canonical.encode())])
    key = _hmac(("AWS4" + creds["SecretKey"]).encode(), date)
    for part in (region, "execute-api", "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "x-amz-date": amz_date,
        "x-amz-security-token": token,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={creds['AccessKeyId']}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}"
        ),
    }


class IRobotCloud:
    """Una sessione con il cloud iRobot."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str, country: str = "IT") -> None:
        self._session = session
        self._username = username
        self._password = password
        self._country = country or "IT"
        self._app_id = f"ANDROID-{uuid.uuid4()}"
        self._base: str = ""
        self._region: str = ""
        self._creds: dict[str, str] = {}
        self._login_at = 0.0
        self.robots: dict[str, dict[str, Any]] = {}

    async def login(self) -> dict[str, dict[str, Any]]:
        """Esegue il login e restituisce i robot dell'account (blid -> dati)."""
        try:
            async with self._session.get(
                DISCOVERY_URL, params={"country_code": self._country}, timeout=TIMEOUT
            ) as resp:
                disc = await resp.json(content_type=None)
            deployment = disc["deployments"][disc["current_deployment"]]
            self._base = deployment.get("httpBaseAuth") or deployment["httpBase"]
            http_base = deployment["httpBase"]
            self._region = deployment["awsRegion"]
            gigya = disc["gigya"]

            async with self._session.post(
                f"https://accounts.{gigya['datacenter_domain']}/accounts.login",
                data={
                    "apiKey": gigya["api_key"], "targetenv": "mobile",
                    "loginID": self._username, "password": self._password, "format": "json",
                },
                timeout=TIMEOUT,
            ) as resp:
                gig = await resp.json(content_type=None)
            if gig.get("errorCode"):
                raise CloudAuthError(gig.get("errorMessage") or "login rifiutato")

            async with self._session.post(
                f"{http_base}/v2/login",
                json={
                    "app_id": self._app_id, "assume_robot_ownership": "0",
                    "gigya": {
                        "signature": gig["UIDSignature"],
                        "timestamp": gig["signatureTimestamp"],
                        "uid": gig["UID"],
                    },
                },
                timeout=TIMEOUT,
            ) as resp:
                data = await resp.json(content_type=None)
            if "credentials" not in data:
                raise CloudAuthError(str(data.get("message") or data)[:200])
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as err:
            raise CloudError(f"cloud iRobot non raggiungibile: {err!r}") from err
        self._creds = data["credentials"]
        self.robots = data.get("robots") or {}
        self._login_at = time.monotonic()
        return self.robots

    async def _get(self, path: str, retry: bool = True) -> Any:
        if not self._creds or time.monotonic() - self._login_at > 3000:
            await self.login()
        url = f"{self._base}{path}"
        headers = sign_request("GET", url, self._region, self._creds)
        try:
            async with self._session.get(URL(url, encoded=True), headers=headers, timeout=TIMEOUT) as resp:
                if resp.status in (401, 403) and retry:
                    self._creds = {}
                    return await self._get(path, retry=False)
                if resp.status >= 400:
                    raise CloudError(f"HTTP {resp.status} su {path}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise CloudError(f"richiesta fallita: {err!r}") from err

    async def get_pmaps(self, blid: str) -> list[dict[str, Any]]:
        data = await self._get(f"/v1/{blid}/pmaps?activeDetails=2")
        return data if isinstance(data, list) else []

    async def get_umf(self, blid: str, pmap_id: str, version: str) -> dict[str, Any]:
        data = await self._get(f"/v1/{blid}/pmaps/{pmap_id}/versions/{version}/umf?activeDetails=2")
        return data if isinstance(data, dict) else {}


def parse_map(pmaps: list[dict[str, Any]], umfs: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Riduce pmaps + UMF alla mappa che usa Scove (coordinate in mm).

    Sceglie la mappa aggiornata più di recente. Stanze e zone senza
    geometria restano utilizzabili per la pulizia, solo non disegnate.
    """
    best: dict[str, Any] | None = None
    for pmap in pmaps:
        details = pmap.get("active_pmapv_details") or {}
        pmapv = details.get("active_pmapv") or {}
        pmap_id = pmapv.get("pmap_id") or pmap.get("pmap_id")
        if not pmap_id:
            continue
        version = pmapv.get("last_user_pmapv_id") or pmapv.get("pmapv_id") or pmap.get("active_pmapv_id")
        ts = pmapv.get("last_user_ts") or pmap.get("last_pmapv_ts") or 0
        if best is None or ts > best["_ts"]:
            best = {
                "_ts": ts, "pmap_id": pmap_id, "version": version,
                "name": (details.get("map_header") or {}).get("name") or "Mappa",
                "regions": details.get("regions") or [], "zones": details.get("zones") or [],
            }
    if best is None:
        return None

    points: dict[str, tuple[float, float]] = {}
    geo_regions: dict[str, list] = {}
    keepouts_raw: list[dict] = []
    for m in (umfs.get(best["pmap_id"]) or {}).get("maps") or []:
        for p in m.get("points2d") or []:
            c = p.get("coordinates") or []
            if p.get("id") is not None and len(c) >= 2:
                points[str(p["id"])] = (float(c[0]) * 1000.0, float(c[1]) * 1000.0)
        for r in m.get("regions") or []:
            geo_regions[str(r.get("id") or r.get("region_id"))] = (r.get("geometry") or {}).get("ids") or []
        keepouts_raw.extend(m.get("keepoutzones") or [])

    def poly(ids_lists: list) -> list[list[float]]:
        ring = ids_lists[0] if ids_lists and isinstance(ids_lists[0], list) else ids_lists
        return [[round(points[str(i)][0]), round(points[str(i)][1])] for i in ring or [] if str(i) in points]

    rooms = []
    for r in best["regions"]:
        rid = str(r.get("id") or r.get("region_id"))
        rooms.append({
            "id": rid, "type": "rid", "name": r.get("name") or f"Stanza {rid}",
            "polygon": poly(geo_regions.get(rid, [])),
        })
    zones = [
        {"id": str(z.get("id") or z.get("zone_id")), "type": "zid", "name": z.get("name") or "Zona"}
        for z in best["zones"]
    ]
    keepouts = [p for p in (poly((k.get("geometry") or {}).get("ids") or []) for k in keepouts_raw) if len(p) >= 3]
    return {
        "pmap_id": best["pmap_id"], "version": best["version"], "name": best["name"],
        "rooms": rooms, "zones": zones, "keepouts": keepouts,
    }
