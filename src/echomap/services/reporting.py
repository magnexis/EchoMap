from __future__ import annotations

import csv
import html
import json
from pathlib import Path


def export_json(graph: dict, output: Path) -> Path:
    output.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    return output


def export_markdown(graph: dict, output: Path) -> Path:
    lines = ["# EchoMap Report", "", "## Nodes", ""]
    for node in graph.get("nodes", []):
        lines.append(f"- {node['label']} ({node['kind']})")
    lines.extend(["", "## Edges", ""])
    for edge in graph.get("edges", []):
        lines.append(f"- {edge['source']} --{edge['relation']}--> {edge['target']} ({edge['confidence']:.2f})")
    if graph.get("stats"):
        lines.extend(["", "## Stats", ""])
        for key, value in graph["stats"].items():
            lines.append(f"- {key}: {value}")
    if graph.get("bookmarks"):
        lines.extend(["", "## Bookmarks", ""])
        for bookmark in graph["bookmarks"]:
            lines.append(f"- {bookmark['label']} ({bookmark['kind']})")
    if graph.get("investigations"):
        lines.extend(["", "## Investigations", ""])
        for investigation in graph["investigations"]:
            lines.append(f"- {investigation['title']} | {investigation['query']}")
    if graph.get("comparisons"):
        lines.extend(["", "## Comparisons", ""])
        for comparison in graph["comparisons"]:
            lines.append(f"- {comparison['left_id']} vs {comparison['right_id']}")
    if graph.get("annotations"):
        lines.extend(["", "## Annotations", ""])
        for annotation in graph["annotations"]:
            lines.append(f"- {annotation['title']} | {annotation['target_type']}:{annotation['target_id']}")
    if graph.get("latest_discovery_profile"):
        lines.extend(["", "## Latest Tech DNA", ""])
        profile = graph["latest_discovery_profile"]
        lines.append(f"- Summary: {profile.get('summary', 'n/a')}")
        lines.append(f"- Confidence: {profile.get('confidence_score', 'n/a')}")
        for category, entries in profile.get("categories", {}).items():
            if entries:
                lines.append(f"- {category}: {', '.join(entry['technology'] for entry in entries[:4])}")
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def export_html(graph: dict, output: Path) -> Path:
    node_rows = "".join(
        f"<tr><td>{n['label']}</td><td>{n['kind']}</td><td>{json.dumps(n['metadata'])}</td></tr>"
        for n in graph.get("nodes", [])
    )
    edge_rows = "".join(
        f"<tr><td>{e['source']}</td><td>{e['relation']}</td><td>{e['target']}</td><td>{e['confidence']:.2f}</td></tr>"
        for e in graph.get("edges", [])
    )
    html = f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>EchoMap Report</title>
        <style>
          body {{ font-family: Arial, sans-serif; background: #111827; color: #e5e7eb; padding: 24px; }}
          table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
          th, td {{ border: 1px solid #374151; padding: 8px; text-align: left; vertical-align: top; }}
          th {{ background: #1f2937; }}
        </style>
      </head>
      <body>
        <h1>EchoMap Report</h1>
        <h2>Nodes</h2>
        <table><thead><tr><th>Label</th><th>Kind</th><th>Metadata</th></tr></thead><tbody>{node_rows}</tbody></table>
        <h2>Edges</h2>
        <table><thead><tr><th>Source</th><th>Relation</th><th>Target</th><th>Confidence</th></tr></thead><tbody>{edge_rows}</tbody></table>
        <h2>Workspace Stats</h2>
        <pre>{json.dumps(graph.get("stats", {}), indent=2)}</pre>
        <h2>Bookmarks</h2>
        <pre>{json.dumps(graph.get("bookmarks", []), indent=2)}</pre>
        <h2>Investigations</h2>
        <pre>{json.dumps(graph.get("investigations", []), indent=2)}</pre>
        <h2>Comparisons</h2>
        <pre>{json.dumps(graph.get("comparisons", []), indent=2)}</pre>
        <h2>Annotations</h2>
        <pre>{json.dumps(graph.get("annotations", []), indent=2)}</pre>
        <h2>Latest Tech DNA</h2>
        <pre>{json.dumps(graph.get("latest_discovery_profile", {}), indent=2)}</pre>
      </body>
    </html>
    """
    output.write_text(html, encoding="utf-8")
    return output


def export_csv(graph: dict, nodes_output: Path, edges_output: Path) -> tuple[Path, Path]:
    with nodes_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "label", "kind", "metadata", "created_at"])
        for node in graph.get("nodes", []):
            writer.writerow([node["id"], node["label"], node["kind"], json.dumps(node["metadata"]), node["created_at"]])
    with edges_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "source", "target", "relation", "confidence", "metadata", "created_at"])
        for edge in graph.get("edges", []):
            writer.writerow(
                [
                    edge["id"],
                    edge["source"],
                    edge["target"],
                    edge["relation"],
                    edge["confidence"],
                    json.dumps(edge["metadata"]),
                    edge["created_at"],
                ]
            )
    return nodes_output, edges_output


def export_report_markdown(report: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {report.get('title', 'EchoMap Report')}"]
    subtitle = report.get("subtitle")
    if subtitle:
        lines.extend(["", subtitle])
    generated_at = report.get("generated_at")
    if generated_at:
        lines.extend(["", f"_Generated at {generated_at}_"])

    summary = report.get("summary")
    if summary:
        lines.extend(["", "## Summary", "", summary])

    graph = report.get("graph")
    if graph:
        lines.extend(["", "## Workspace Stats", ""])
        for key, value in graph.get("stats", {}).items():
            lines.append(f"- {key}: {value}")
        if graph.get("nodes"):
            lines.extend(["", "## Nodes", ""])
            for node in graph["nodes"][:200]:
                lines.append(f"- {node['label']} ({node['kind']})")
        if graph.get("edges"):
            lines.extend(["", "## Edges", ""])
            for edge in graph["edges"][:200]:
                lines.append(f"- {edge['source']} --{edge['relation']}--> {edge['target']} ({edge['confidence']:.2f})")
        if graph.get("bookmarks"):
            lines.extend(["", "## Bookmarks", ""])
            for bookmark in graph["bookmarks"]:
                lines.append(f"- {bookmark['label']} ({bookmark['kind']}) | {bookmark['note']}")
        if graph.get("annotations"):
            lines.extend(["", "## Annotations", ""])
            for annotation in graph["annotations"]:
                lines.append(f"- {annotation['title']} | {annotation['target_type']}:{annotation['target_id']}")
        if graph.get("investigations"):
            lines.extend(["", "## Investigations", ""])
            for investigation in graph["investigations"]:
                lines.append(f"- {investigation['title']} | {investigation['query']}")
        if graph.get("comparisons"):
            lines.extend(["", "## Comparisons", ""])
            for comparison in graph["comparisons"]:
                lines.append(f"- {comparison['left_id']} vs {comparison['right_id']}")
        if graph.get("latest_discovery_profile"):
            profile = graph["latest_discovery_profile"]
            lines.extend(["", "## Latest Tech DNA", ""])
            lines.append(f"- Summary: {profile.get('summary', 'n/a')}")
            lines.append(f"- Confidence: {profile.get('confidence_score', 'n/a')}")
            for category, entries in profile.get("categories", {}).items():
                if entries:
                    lines.append(f"- {category}: {', '.join(entry['technology'] for entry in entries[:4])}")

    investigation = report.get("investigation")
    if investigation:
        lines.extend(["", "## Investigation", ""])
        lines.append(f"- Title: {investigation.get('title', 'n/a')}")
        lines.append(f"- Query: {investigation.get('query', 'n/a')}")
        lines.append(f"- Tags: {investigation.get('tags', '') or 'none'}")
        lines.append(f"- Notes: {investigation.get('notes', '') or 'none'}")
        lines.append(f"- Created At: {investigation.get('created_at', 'n/a')}")

    comparison = report.get("comparison")
    if comparison:
        lines.extend(["", "## Comparison", ""])
        lines.append(f"- Summary: {comparison.get('summary', 'n/a')}")
        if "score" in comparison:
            lines.append(f"- Score: {comparison.get('score')}")
        if "overlap_score" in comparison:
            lines.append(f"- Overlap Score: {comparison.get('overlap_score')}")
        if comparison.get("left_title"):
            lines.append(f"- Left: {comparison.get('left_title')}")
        if comparison.get("right_title"):
            lines.append(f"- Right: {comparison.get('right_title')}")
        if comparison.get("shared_relations"):
            lines.append(f"- Shared Relations: {', '.join(comparison['shared_relations'])}")
        if comparison.get("shared_nodes"):
            lines.append(f"- Shared Nodes: {', '.join(comparison['shared_nodes'][:12])}")
        if comparison.get("shared_edges"):
            lines.append(f"- Shared Edges: {', '.join(comparison['shared_edges'][:12])}")

    discovery = report.get("discovery")
    if discovery:
        lines.extend(["", "## Discovery", ""])
        lines.append(f"- Root Query: {discovery.get('query', 'n/a')}")
        lines.append(f"- Summary: {discovery.get('summary', 'n/a')}")
        if discovery.get("technologies"):
            lines.append(f"- Technologies: {', '.join(item.get('technology', 'unknown') for item in discovery['technologies'][:10])}")
        if discovery.get("related_targets"):
            lines.append(f"- Related Targets: {', '.join(discovery['related_targets'][:10])}")
        if discovery.get("tech_profile"):
            lines.append(f"- Tech Profile: {discovery['tech_profile'].get('summary', 'n/a')}")

    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def export_report_html(report: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    def block(title: str, body: str) -> str:
        return f"<section><h2>{html.escape(title)}</h2>{body}</section>"

    sections: list[str] = []
    title = html.escape(report.get("title", "EchoMap Report"))
    subtitle = report.get("subtitle")
    generated_at = report.get("generated_at")
    header = [f"<h1>{title}</h1>"]
    if subtitle:
        header.append(f"<p>{html.escape(subtitle)}</p>")
    if generated_at:
        header.append(f"<p><em>Generated at {html.escape(str(generated_at))}</em></p>")

    summary = report.get("summary")
    if summary:
        sections.append(block("Summary", f"<pre>{html.escape(str(summary))}</pre>"))

    graph = report.get("graph")
    if graph:
        stats_html = "<ul>" + "".join(f"<li>{html.escape(str(k))}: {html.escape(str(v))}</li>" for k, v in graph.get("stats", {}).items()) + "</ul>"
        sections.append(block("Workspace Stats", stats_html))
        if graph.get("nodes"):
            rows = "".join(
                f"<tr><td>{html.escape(node['label'])}</td><td>{html.escape(node['kind'])}</td><td>{html.escape(json.dumps(node['metadata']))}</td></tr>"
                for node in graph["nodes"][:200]
            )
            sections.append(
                block(
                    "Nodes",
                    f"<table><thead><tr><th>Label</th><th>Kind</th><th>Metadata</th></tr></thead><tbody>{rows}</tbody></table>",
                )
            )
        if graph.get("edges"):
            rows = "".join(
                f"<tr><td>{html.escape(edge['source'])}</td><td>{html.escape(edge['relation'])}</td><td>{html.escape(edge['target'])}</td><td>{edge['confidence']:.2f}</td></tr>"
                for edge in graph["edges"][:200]
            )
            sections.append(
                block(
                    "Edges",
                    f"<table><thead><tr><th>Source</th><th>Relation</th><th>Target</th><th>Confidence</th></tr></thead><tbody>{rows}</tbody></table>",
                )
            )
        if graph.get("bookmarks"):
            sections.append(block("Bookmarks", f"<pre>{html.escape(json.dumps(graph.get('bookmarks', []), indent=2))}</pre>"))
        if graph.get("annotations"):
            sections.append(block("Annotations", f"<pre>{html.escape(json.dumps(graph.get('annotations', []), indent=2))}</pre>"))
        if graph.get("investigations"):
            sections.append(block("Investigations", f"<pre>{html.escape(json.dumps(graph.get('investigations', []), indent=2))}</pre>"))
        if graph.get("comparisons"):
            sections.append(block("Comparisons", f"<pre>{html.escape(json.dumps(graph.get('comparisons', []), indent=2))}</pre>"))
        if graph.get("latest_discovery_profile"):
            sections.append(block("Latest Tech DNA", f"<pre>{html.escape(json.dumps(graph.get('latest_discovery_profile', {}), indent=2))}</pre>"))

    investigation = report.get("investigation")
    if investigation:
        body = "<pre>" + html.escape(json.dumps(investigation, indent=2)) + "</pre>"
        sections.append(block("Investigation", body))

    comparison = report.get("comparison")
    if comparison:
        body = "<pre>" + html.escape(json.dumps(comparison, indent=2)) + "</pre>"
        sections.append(block("Comparison", body))

    discovery = report.get("discovery")
    if discovery:
        body = "<pre>" + html.escape(json.dumps(discovery, indent=2)) + "</pre>"
        sections.append(block("Discovery", body))

    html_doc = f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>{title}</title>
        <style>
          body {{ font-family: Arial, sans-serif; background: #111827; color: #e5e7eb; padding: 24px; }}
          section {{ margin: 24px 0; padding: 18px; border: 1px solid #374151; border-radius: 12px; background: #0f172a; }}
          table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
          th, td {{ border: 1px solid #374151; padding: 8px; text-align: left; vertical-align: top; }}
          th {{ background: #1f2937; }}
          pre {{ white-space: pre-wrap; word-break: break-word; }}
        </style>
      </head>
      <body>
        {''.join(header)}
        {''.join(sections)}
      </body>
    </html>
    """
    output.write_text(html_doc, encoding="utf-8")
    return output
