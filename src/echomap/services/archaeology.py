from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

import requests


@dataclass(slots=True)
class ArchaeologyBundle:
    snapshots: list[dict] = field(default_factory=list)
    dns_records: list[dict] = field(default_factory=list)
    certificates: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    related_targets: list[str] = field(default_factory=list)


def _request_json(url: str, timeout: float = 8.0) -> object:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "EchoMap/0.1 (+https://magnexis.local)"},
    )
    response.raise_for_status()
    return response.json()


def wayback_bundle(target_url: str, limit: int = 10) -> list[dict]:
    api = (
        "https://web.archive.org/cdx/search/cdx"
        f"?url={quote(target_url, safe='')}"
        "&output=json&fl=timestamp,original,statuscode,mimetype&filter=statuscode:200&collapse=digest"
    )
    try:
        payload = _request_json(api)
    except Exception:
        return []

    snapshots: list[dict] = []
    for row in payload[1 : limit + 1]:
        timestamp, original, status_code, mimetype = row[:4]
        snapshots.append(
            {
                "type": "wayback_snapshot",
                "timestamp": timestamp,
                "original": original,
                "status_code": status_code,
                "mimetype": mimetype,
                "archive_url": f"https://web.archive.org/web/{timestamp}/{original}",
            }
        )
    return snapshots


def wayback_available(target_url: str) -> list[dict]:
    api = f"https://archive.org/wayback/available?url={quote(target_url, safe='')}"
    try:
        payload = _request_json(api)
    except Exception:
        return []
    snapshot = payload.get("archived_snapshots", {}).get("closest")
    if not snapshot:
        return []
    return [
        {
            "type": "wayback_available",
            "timestamp": snapshot.get("timestamp"),
            "available": snapshot.get("available"),
            "status": snapshot.get("status"),
            "archive_url": snapshot.get("url"),
        }
    ]


def dns_snapshot(domain: str) -> list[dict]:
    # Best-effort live DNS snapshot that gets stored over time so users can compare scans.
    record_types = ["A", "AAAA", "MX", "NS", "TXT"]
    records: list[dict] = []
    for record_type in record_types:
        api = f"https://dns.google/resolve?name={quote(domain, safe='')}&type={record_type}"
        try:
            payload = _request_json(api)
        except Exception:
            continue
        answer = payload.get("Answer", []) if isinstance(payload, dict) else []
        for item in answer[:8]:
            records.append(
                {
                    "type": "dns_record",
                    "record_type": record_type,
                    "name": item.get("name", domain),
                    "value": item.get("data"),
                    "ttl": item.get("TTL"),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    return records


def certificate_history(domain: str, limit: int = 30) -> list[dict]:
    api = f"https://crt.sh/?q=%25.{quote(domain, safe='')}&output=json"
    try:
        payload = _request_json(api)
    except Exception:
        return []
    certificates: list[dict] = []
    if not isinstance(payload, list):
        return certificates
    seen: set[tuple[str, str]] = set()
    for row in payload[:limit]:
        entry = (
            row.get("issuer_name", ""),
            row.get("common_name", ""),
        )
        if entry in seen:
            continue
        seen.add(entry)
        certificates.append(
            {
                "type": "certificate",
                "issuer_name": row.get("issuer_name"),
                "common_name": row.get("common_name"),
                "name_value": row.get("name_value"),
                "not_before": row.get("not_before"),
                "not_after": row.get("not_after"),
                "entry_timestamp": row.get("entry_timestamp"),
            }
        )
    return certificates


def build_archaeology_bundle(domain: str, target_url: str | None = None) -> ArchaeologyBundle:
    target_url = target_url or f"https://{domain}"
    snapshots = wayback_available(target_url) + wayback_bundle(target_url)
    dns_records = dns_snapshot(domain)
    certificates = certificate_history(domain)

    related_targets = sorted(
        {
            row["name_value"]
            for row in certificates
            if isinstance(row.get("name_value"), str) and row["name_value"]
        }
    )
    timeline = []
    if snapshots:
        timeline.append(
            {
                "date": snapshots[0].get("timestamp", "")[:8] or datetime.now(timezone.utc).date().isoformat(),
                "event": "Archived snapshot found",
                "details": f"{len(snapshots)} wayback snapshot(s) located",
            }
        )
    if dns_records:
        timeline.append(
            {
                "date": datetime.now(timezone.utc).date().isoformat(),
                "event": "DNS snapshot captured",
                "details": f"{len(dns_records)} live DNS record(s) observed",
            }
        )
    if certificates:
        timeline.append(
            {
                "date": certificates[0].get("entry_timestamp", "")[:10] or datetime.now(timezone.utc).date().isoformat(),
                "event": "Certificate history observed",
                "details": f"{len(certificates)} certificate transparency record(s)",
            }
        )

    return ArchaeologyBundle(
        snapshots=snapshots,
        dns_records=dns_records,
        certificates=certificates,
        timeline=timeline,
        related_targets=related_targets,
    )

