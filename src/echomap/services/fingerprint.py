from __future__ import annotations

from html.parser import HTMLParser
import re
from dataclasses import dataclass
from collections import defaultdict
from urllib.parse import urlparse
from typing import Any


TECH_SIGNATURES = {
    "React": [r"react", r"__REACT_DEVTOOLS_GLOBAL_HOOK__"],
    "Next.js": [r"_next/", r"next/script", r"next/data"],
    "Vue": [r"vue", r"__VUE__"],
    "Angular": [r"angular", r"ng-version"],
    "Svelte": [r"svelte", r"/_app/immutable/"],
    "Nuxt": [r"nuxt", r"__NUXT__"],
    "Remix": [r"remix", r"__remix"],
    "Tailwind": [r"tailwind", r"tw-"],
    "FastAPI": [r"fastapi", r"swagger-ui", r"redoc"],
    "Express": [r"express", r"x-powered-by: express"],
    "Django": [r"django", r"csrftoken"],
    "Flask": [r"flask", r"werkzeug"],
    "Node.js": [r"node\.js", r"npm", r"package-lock\.json", r"yarn.lock", r"pnpm-lock.yaml"],
    "Supabase": [r"supabase"],
    "PostgreSQL": [r"postgres", r"pg_"],
    "SQLite": [r"sqlite", r"sqlite3"],
    "Redis": [r"redis"],
    "Railway": [r"railway"],
    "Vercel": [r"vercel", r"x-vercel"],
    "Netlify": [r"netlify", r"x-nf-request-id"],
    "Cloudflare": [r"cloudflare", r"cf-ray", r"cf-cache-status"],
    "Docker": [r"docker", r"containerized"],
    "WordPress": [r"wordpress", r"wp-content", r"wp-includes"],
    "GitHub Pages": [r"github pages", r"\.github\.io"],
}

TECH_CATEGORIES = {
    "React": "frontend",
    "Next.js": "frontend",
    "Vue": "frontend",
    "Angular": "frontend",
    "Svelte": "frontend",
    "Nuxt": "frontend",
    "Remix": "frontend",
    "Tailwind": "frontend",
    "FastAPI": "backend",
    "Express": "backend",
    "Django": "backend",
    "Flask": "backend",
    "Node.js": "backend",
    "Supabase": "backend",
    "PostgreSQL": "database",
    "SQLite": "database",
    "Redis": "database",
    "Railway": "infrastructure",
    "Vercel": "infrastructure",
    "Netlify": "infrastructure",
    "Cloudflare": "infrastructure",
    "Docker": "infrastructure",
    "WordPress": "platform",
    "GitHub Pages": "platform",
}


@dataclass(slots=True)
class FingerprintHit:
    technology: str
    confidence: float
    evidence: str


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.in_title = False
        self.description = ""
        self.open_graph: dict[str, str] = {}
        self.generator = ""
        self.canonical = ""
        self.author = ""
        self.theme_color = ""
        self.scripts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_map = {name.lower(): value for name, value in attrs if value is not None}
        if tag.lower() == "title":
            self.in_title = True
        elif tag.lower() == "meta":
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            content = (attr_map.get("content") or "").strip()
            if name in {"description", "og:description"} and content and not self.description:
                self.description = content
            if name == "generator" and content:
                self.generator = content
            if name == "author" and content and not self.author:
                self.author = content
            if name == "theme-color" and content and not self.theme_color:
                self.theme_color = content
            if name.startswith("og:") and content:
                self.open_graph[name] = content
        elif tag.lower() == "script":
            src = attr_map.get("src")
            if src:
                self.scripts.append(src)
        elif tag.lower() == "link":
            href = attr_map.get("href")
            if href:
                self.links.append(href)
            rel = (attr_map.get("rel") or "").lower()
            if rel == "canonical" and href and not self.canonical:
                self.canonical = href

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


def normalize_host(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower() or parsed.path.lower()


def extract_signals(html: str, headers: dict[str, str], url: str) -> list[FingerprintHit]:
    haystack = " ".join([html, " ".join(f"{k}: {v}" for k, v in headers.items()), url]).lower()
    hits: list[FingerprintHit] = []
    for tech, patterns in TECH_SIGNATURES.items():
        evidence = next((pattern for pattern in patterns if re.search(pattern, haystack)), None)
        if evidence:
            confidence = 0.94 if tech in {"React", "Next.js", "FastAPI", "Supabase", "PostgreSQL"} else 0.82
            hits.append(FingerprintHit(tech, confidence, evidence))
    return hits


def build_stack_profile(hits: list[FingerprintHit], metadata: dict[str, Any] | None = None, headers: dict[str, str] | None = None, url: str | None = None) -> dict[str, Any]:
    metadata = metadata or {}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ordered_categories = ("frontend", "backend", "database", "infrastructure", "platform", "other")
    for hit in hits:
        category = TECH_CATEGORIES.get(hit.technology, "other")
        buckets[category].append(
            {
                "technology": hit.technology,
                "confidence": hit.confidence,
                "evidence": hit.evidence,
            }
        )

    for category in ordered_categories:
        buckets.setdefault(category, [])

    top_hits = sorted(hits, key=lambda item: (-item.confidence, item.technology))[:5]
    if top_hits:
        primary = ", ".join(hit.technology for hit in top_hits[:3])
        summary_bits = []
        for category in ordered_categories:
            names = [entry["technology"] for entry in buckets[category]]
            if names:
                summary_bits.append(f"{category}: {', '.join(names[:3])}")
        summary = "Detected " + "; ".join(summary_bits) if summary_bits else f"Detected {primary}."
    else:
        primary = ""
        summary = "No technology fingerprints detected."

    average_confidence = sum(hit.confidence for hit in hits) / max(1, len(hits))
    category_count = sum(1 for category in ordered_categories if buckets[category])
    confidence_score = round(min(99.0, (average_confidence * 100) + (category_count * 2.5)), 1)

    dna = {
        "signals": len(hits),
        "categories": {category: len(buckets[category]) for category in ordered_categories},
        "primary": primary,
        "confidence_score": confidence_score,
    }
    if metadata.get("generator"):
        dna["generator"] = metadata["generator"]
    if metadata.get("canonical"):
        dna["canonical"] = metadata["canonical"]
    if metadata.get("theme_color"):
        dna["theme_color"] = metadata["theme_color"]
    if headers:
        server = headers.get("server") or headers.get("x-powered-by")
        if server:
            dna["server_hint"] = server
    if url:
        dna["url"] = normalize_host(url)

    return {
        "summary": summary,
        "confidence_score": confidence_score,
        "dna": dna,
        "primary_stack": top_hits[0].technology if top_hits else "",
        "categories": {category: list(buckets[category]) for category in ordered_categories},
        "hits": [
            {
                "technology": hit.technology,
                "confidence": hit.confidence,
                "evidence": hit.evidence,
            }
            for hit in top_hits
        ],
    }


def parse_html_metadata(html: str) -> dict:
    parser = _MetadataParser()
    parser.feed(html or "")
    return {
        "title": "".join(parser.title_parts).strip(),
        "description": parser.description,
        "open_graph": parser.open_graph,
        "generator": parser.generator,
        "canonical": parser.canonical,
        "author": parser.author,
        "theme_color": parser.theme_color,
        "scripts": parser.scripts[:50],
        "links": parser.links[:50],
    }
