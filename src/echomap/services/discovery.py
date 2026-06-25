from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from ..models import DiscoveryResult, Edge, Node
from .archaeology import build_archaeology_bundle
from .fingerprint import build_stack_profile, extract_signals, parse_html_metadata
from .relationship import domain_from_url, parent_domains


def _node_id(kind: str, label: str) -> str:
    digest = hashlib.sha1(f"{kind}:{label}".encode("utf-8")).hexdigest()[:14]
    return f"{kind}:{digest}"


def _edge_id(source: str, target: str, relation: str) -> str:
    digest = hashlib.sha1(f"{source}->{target}:{relation}".encode("utf-8")).hexdigest()[:16]
    return f"edge:{digest}"


def _safe_get(url: str, timeout: float = 8.0) -> tuple[str, dict[str, str], int]:
    # Validate URL protocol
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

    # Reject private/internal IP ranges via hostname
    import socket
    hostname = parsed.hostname
    if hostname:
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addr_infos = socket.getaddrinfo(hostname, port)
            for _family, _type, _proto, _canonname, sockaddr in addr_infos:
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                    raise ValueError(f"URL resolves to a private/internal IP address: {ip}")
        except socket.gaierror:
            pass  # DNS resolution failed; the fetch will fail naturally

    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "EchoMap/0.1 (+https://magnexis.local)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    content_type = response.headers.get("content-type", "")
    if "text" not in content_type and "json" not in content_type and response.text.strip() == "":
        return "", dict(response.headers), response.status_code
    return response.text, dict(response.headers), response.status_code


def _title_to_company(title: str) -> str | None:
    cleaned = re.sub(r"\s*[-|]\s*.*$", "", title).strip()
    return cleaned or None


def _discover_related_targets(html: str, metadata: dict, host: str, url: str) -> list[str]:
    related: set[str] = set()
    for link in metadata.get("scripts", []) + metadata.get("links", []):
        if not isinstance(link, str):
            continue
        if link.startswith("http"):
            related.add(link)
        elif link.startswith("/"):
            related.add(f"https://{host}{link}")
    for match in re.findall(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", html, re.IGNORECASE):
        related.add(match.rstrip(").,;\"'"))
    for match in re.findall(r"https://github\.com/[A-Za-z0-9_.-]+", html, re.IGNORECASE):
        related.add(match.rstrip(").,;\"'"))
    if metadata.get("open_graph", {}).get("og:site_name"):
        related.add(metadata["open_graph"]["og:site_name"])
    if metadata.get("title"):
        company = _title_to_company(metadata["title"])
        if company and company.lower() != host.lower():
            related.add(company)
    for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", html):
        related.add(email.lower())
    return sorted(related)


def discover(query: str) -> DiscoveryResult:
    normalized = query.strip()
    nodes: dict[str, Node] = {}
    edges: dict[str, Edge] = {}
    timeline: list[dict[str, Any]] = []
    technologies: list[dict[str, Any]] = []
    archaeology: list[dict[str, Any]] = []
    summary_parts: list[str] = []
    related_targets: list[str] = []
    tech_profile: dict[str, Any] = {}

    if normalized.startswith("http://") or normalized.startswith("https://") or "." in normalized:
        url = normalized if normalized.startswith("http") else f"https://{normalized}"
        host = domain_from_url(url)
        root_id = _node_id("website", host)
        domain_id = _node_id("domain", host)
        nodes[root_id] = Node(id=root_id, label=host, kind="Website", metadata={"url": url, "host": host})
        nodes[domain_id] = Node(id=domain_id, label=host, kind="Domain", metadata={"host": host})
        edges[_edge_id(root_id, domain_id, "resolved_to")] = Edge(
            id=_edge_id(root_id, domain_id, "resolved_to"),
            source=root_id,
            target=domain_id,
            relation="resolved_to",
            confidence=1.0,
        )
        summary_parts.append(f"Resolved website/domain target: {host}")
        try:
            html, headers, status = _safe_get(url)
            metadata = parse_html_metadata(html)
            nodes[root_id].metadata.update(
                {
                    "status": status,
                    "title": metadata["title"],
                    "description": metadata["description"],
                    "headers": {k.lower(): v for k, v in list(headers.items())[:50]},
                }
            )
            if metadata["title"]:
                summary_parts.append(f"Page title: {metadata['title']}")

            company_name = _title_to_company(metadata["title"])
            if company_name and company_name.lower() != host.lower():
                company_id = _node_id("company", company_name)
                nodes.setdefault(
                    company_id,
                    Node(
                        id=company_id,
                        label=company_name,
                        kind="Company",
                        metadata={"source": url, "title": metadata["title"]},
                    ),
                )
                company_edge_id = _edge_id(root_id, company_id, "owned_by")
                edges[company_edge_id] = Edge(
                    id=company_edge_id,
                    source=root_id,
                    target=company_id,
                    relation="owned_by",
                    confidence=0.45,
                    metadata={"evidence": "page title"},
                )

            hits = extract_signals(html, headers, url)
            tech_profile = build_stack_profile(hits, metadata, headers, url)
            nodes[root_id].metadata["tech_profile"] = tech_profile
            if tech_profile.get("summary"):
                summary_parts.append(tech_profile["summary"])
            for hit in hits:
                tech_id = _node_id("technology", hit.technology)
                if tech_id not in nodes:
                    nodes[tech_id] = Node(
                        id=tech_id,
                        label=hit.technology,
                        kind="Technology",
                        metadata={"confidence": hit.confidence, "evidence": hit.evidence},
                    )
                edge_id = _edge_id(root_id, tech_id, "built_with")
                edges[edge_id] = Edge(
                    id=edge_id,
                    source=root_id,
                    target=tech_id,
                    relation="built_with",
                    confidence=hit.confidence,
                    metadata={"evidence": hit.evidence},
                )
                technologies.append(
                    {
                        "technology": hit.technology,
                        "confidence": hit.confidence,
                        "evidence": hit.evidence,
                    }
                )

            related_targets = _discover_related_targets(html, metadata, host, url)
            for link in related_targets[:25]:
                if link.startswith("http") and host not in link:
                    linked_host = domain_from_url(link)
                    linked_id = _node_id("website", linked_host)
                    if linked_id not in nodes:
                        nodes[linked_id] = Node(
                            id=linked_id,
                            label=linked_host,
                            kind="Website",
                            metadata={"source": link},
                        )
                    edge_id = _edge_id(root_id, linked_id, "connected_to")
                    edges[edge_id] = Edge(
                        id=edge_id,
                        source=root_id,
                        target=linked_id,
                        relation="connected_to",
                        confidence=0.55,
                        metadata={"source": link},
                    )
                elif link.endswith(f"@{host}") or "@" in link:
                    person_label = link
                    person_id = _node_id("person", person_label)
                    nodes.setdefault(person_id, Node(id=person_id, label=person_label, kind="Person", metadata={"source": link}))
                    edge_id = _edge_id(root_id, person_id, "references")
                    edges[edge_id] = Edge(
                        id=edge_id,
                        source=root_id,
                        target=person_id,
                        relation="references",
                        confidence=0.4,
                        metadata={"source": link},
                    )
                elif link and "." in link and not link.startswith("http"):
                    subdomain = link.lower()
                    if subdomain.endswith(host) and subdomain != host:
                        child_id = _node_id("domain", subdomain)
                        nodes.setdefault(child_id, Node(id=child_id, label=subdomain, kind="Domain", metadata={"parent": host}))
                        edge_id = _edge_id(domain_id, child_id, "child_domain")
                        edges[edge_id] = Edge(
                            id=edge_id,
                            source=domain_id,
                            target=child_id,
                            relation="child_domain",
                            confidence=0.82,
                        )

            for parent in parent_domains(host):
                parent_id = _node_id("domain", parent)
                nodes.setdefault(parent_id, Node(id=parent_id, label=parent, kind="Domain", metadata={}))
                edge_id = _edge_id(domain_id, parent_id, "parent_domain")
                edges[edge_id] = Edge(
                    id=edge_id,
                    source=domain_id,
                    target=parent_id,
                    relation="parent_domain",
                    confidence=0.9,
                )

            timeline.append(
                {
                    "date": datetime.now(timezone.utc).date().isoformat(),
                    "event": "Discovery completed",
                    "details": f"Fetched {url} and fingerprinted technologies.",
                }
            )
            archaeology.append(
                {
                    "type": "snapshot",
                    "label": "Current live snapshot",
                    "details": {
                        "status": status,
                        "url": url,
                        "title": metadata["title"],
                    },
                }
            )
            archive_bundle = build_archaeology_bundle(host, url)
            archaeology.extend(archive_bundle.snapshots)
            archaeology.extend(archive_bundle.dns_records)
            archaeology.extend(archive_bundle.certificates)
            timeline.extend(archive_bundle.timeline)
            related_targets.extend(archive_bundle.related_targets)
        except (ValueError, requests.RequestException) as exc:
            summary_parts.append(f"Fetch failed: {exc.__class__.__name__}")
            archaeology.append(
                {
                    "type": "error",
                    "label": "Live fetch failed",
                    "details": str(exc),
                }
            )

    elif "github.com/" in normalized.lower() or normalized.lower().startswith("github "):
        target = normalized if "github.com" in normalized else normalized.split(" ", 1)[-1]
        repo_slug = target.split("github.com/")[-1].strip("/")
        repo_id = _node_id("repository", repo_slug)
        org = repo_slug.split("/")[0]
        org_id = _node_id("organization", org)
        nodes[repo_id] = Node(id=repo_id, label=repo_slug, kind="Repository", metadata={"source": target})
        nodes[org_id] = Node(id=org_id, label=org, kind="GitHub Organization", metadata={})
        edges[_edge_id(repo_id, org_id, "owned_by")] = Edge(
            id=_edge_id(repo_id, org_id, "owned_by"),
            source=repo_id,
            target=org_id,
            relation="owned_by",
            confidence=0.9,
        )
        summary_parts.append(f"Identified GitHub repository: {repo_slug}")
        try:
            api_url = f"https://api.github.com/repos/{repo_slug}"
            response = requests.get(api_url, timeout=8.0, headers={"Accept": "application/vnd.github+json"})
            if response.ok:
                payload = response.json()
                nodes[repo_id].metadata.update(
                    {
                        "stars": payload.get("stargazers_count", 0),
                        "forks": payload.get("forks_count", 0),
                        "language": payload.get("language"),
                        "homepage": payload.get("homepage"),
                        "description": payload.get("description"),
                    }
                )
                technologies.append(
                    {
                        "technology": payload.get("language"),
                        "confidence": 0.62,
                        "evidence": "GitHub primary language",
                    }
                )
                timeline.append(
                    {
                        "date": (payload.get("created_at") or "")[:10],
                        "event": "Repository created",
                        "details": repo_slug,
                    }
                )
        except requests.RequestException:
            summary_parts.append("GitHub API lookup unavailable")
        repo_homepage = nodes[repo_id].metadata.get("homepage")
        if isinstance(repo_homepage, str) and repo_homepage.startswith("http"):
            related_targets.append(repo_homepage)
        related_targets.append(f"https://github.com/{repo_slug}")
    else:
        label = normalized
        person_id = _node_id("person", label)
        nodes[person_id] = Node(id=person_id, label=label, kind="Person", metadata={"query": normalized})
        summary_parts.append(f"Created seed node for keyword/person/organization: {normalized}")

    if not nodes:
        fallback_id = _node_id("seed", normalized)
        nodes[fallback_id] = Node(id=fallback_id, label=normalized, kind="Seed", metadata={"query": normalized})

    summary = " | ".join(summary_parts) if summary_parts else "No discovery signals were found."
    return DiscoveryResult(
        root_query=normalized,
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        timeline=timeline,
        technologies=technologies,
        archaeology=archaeology,
        summary=summary,
        tech_profile=tech_profile,
        related_targets=sorted({target for target in related_targets if target}),
    )
