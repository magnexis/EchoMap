from echomap.services.discovery import discover
from echomap.services.fingerprint import build_stack_profile, extract_signals, parse_html_metadata


def test_discover_keyword_seed():
    result = discover("Python")
    assert result.nodes
    assert result.summary


def test_stack_profile_groups_signals():
    html = """
    <html>
      <head>
        <title>EchoMap - Graph Intelligence</title>
        <meta name="generator" content="Next.js" />
        <meta name="theme-color" content="#111827" />
        <link rel="canonical" href="https://echomap.local" />
      </head>
      <body>
        <script src="/_next/static/chunk.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/tailwindcss@latest"></script>
      </body>
    </html>
    """
    metadata = parse_html_metadata(html)
    hits = extract_signals(html, {"server": "Vercel"}, "https://echomap.local")
    profile = build_stack_profile(hits, metadata, {"server": "Vercel"}, "https://echomap.local")
    assert metadata["generator"] == "Next.js"
    assert profile["confidence_score"] > 0
    assert profile["categories"]["frontend"]
    assert "Next.js" in profile["summary"]
