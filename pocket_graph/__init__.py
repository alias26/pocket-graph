"""pocket-graph -- knowledge graph for personal corpora.

Entry point:
    from pocket_graph import run
    run(Path("./my_repo"))
"""
from __future__ import annotations
import json
from pathlib import Path

from .detect import collect_files, classify, FileType, corpus_stats
from .extract import extract
from .build import build, save_graph, to_node_link
from .cluster import cluster
from .analyze import analyze
from .report import render_report
from .languages import supported_languages, supported_extensions

__version__ = "0.1.0"


def run(root: Path,
        out_dir: Path | None = None,
        ignore: list[str] | None = None,
        directed: bool = True,
        export_formats: list[str] | None = None,
        obsidian_vault: Path | None = None) -> dict:
    """End-to-end pipeline: detect -> extract -> build -> cluster -> analyze -> report.
    
    export_formats: list of extra exports to generate. Options:
        'html' (default), 'graphml', 'cypher', 'obsidian'
    Default is ['html'].
    Writes to {out_dir}/graph.json + GRAPH_REPORT.md + extra exports.

    obsidian_vault: if given, write the Obsidian wikilink files into
        <obsidian_vault>/<root.name>/ instead of <out_dir>/obsidian/.
        Falls back to user config (obsidian_vault key) if not given.
    """
    root = Path(root).resolve()
    if out_dir is None:
        out_dir = root / "graph-out"
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. detect
    files = collect_files(root, ignore_globs=ignore or [])
    code_files = [f for f in files if classify(f) == FileType.CODE]
    doc_files = [f for f in files if classify(f) == FileType.DOCUMENT]
    paper_files = [f for f in files if classify(f) == FileType.PAPER]
    print(f"[pocket_graph] {len(files)} files: "
          f"{len(code_files)} code, {len(doc_files)} docs, {len(paper_files)} papers")

    # 2. extract -- code (Pass 1) + docs/papers (Pass 3, deterministic only)
    extractions = extract(code_files + doc_files + paper_files, cache_root=out_dir)
    total_nodes = sum(len(e.get("nodes", [])) for e in extractions)
    total_edges = sum(len(e.get("edges", [])) for e in extractions)
    print(f"[pocket_graph] extracted {total_nodes} nodes, {total_edges} edges")

    # 3. build graph
    G = build(extractions, directed=directed)
    print(f"[pocket_graph] graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # 4. cluster
    communities = cluster(G)
    print(f"[pocket_graph] {len(communities)} communities")

    # 4b. derive hyperedges (class groupings + community groupings)
    from .build import attach_hyperedges
    attach_hyperedges(G, communities)
    he_count = len(G.graph.get("hyperedges", []))
    if he_count:
        class_he = sum(1 for h in G.graph["hyperedges"] if h["type"] == "class_group")
        comm_he = sum(1 for h in G.graph["hyperedges"] if h["type"] == "community")
        print(f"[pocket_graph] {he_count} hyperedges ({class_he} class_group, {comm_he} community)")

    # 5. analyze
    analysis = analyze(G, communities)

    # 6. report
    report_md = render_report(G, analysis)

    # 7. export (optional formats)
    save_graph(G, out_dir / "graph.json")
    (out_dir / "GRAPH_REPORT.md").write_text(report_md, encoding="utf-8")
    (out_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")

    # Save manifest so subsequent `update` runs can do incremental sync.
    # Without this, sync would always see all files as "new" and fall back
    # to full build -- silent loss of the incremental fast path.
    from .sync import save_manifest, _file_hash
    from datetime import datetime, timezone
    manifest = {
        "files": {},
        "last_build": datetime.now(timezone.utc).isoformat(),
    }
    for f in files:
        manifest["files"][str(f)] = {
            "hash": _file_hash(f),
            "last_seen": manifest["last_build"],
        }
    save_manifest(out_dir, manifest)

    formats = export_formats if export_formats is not None else ["html"]
    if "html" in formats:
        try:
            from .export import export_html
            export_html(G, out_dir / "graph.html",
                         title=f"pocket-graph: {root.name}")
            print(f"[pocket_graph] wrote {out_dir}/graph.html")
        except Exception as e:
            print(f"[pocket_graph] HTML export skipped: {e}")
    if "graphml" in formats:
        try:
            from .export import export_graphml
            export_graphml(G, out_dir / "graph.graphml")
            print(f"[pocket_graph] wrote {out_dir}/graph.graphml")
        except Exception as e:
            print(f"[pocket_graph] GraphML export skipped: {e}")
    if "cypher" in formats:
        try:
            from .export import export_neo4j_cypher
            export_neo4j_cypher(G, out_dir / "cypher.txt")
            print(f"[pocket_graph] wrote {out_dir}/cypher.txt")
        except Exception as e:
            print(f"[pocket_graph] Cypher export skipped: {e}")
    if "obsidian" in formats:
        try:
            from .export import export_obsidian
            # Resolve destination: explicit param > <cwd>/llm-wiki/
            if obsidian_vault is not None:
                obs_dest = Path(obsidian_vault) / root.name
            else:
                obs_dest = Path.cwd() / "llm-wiki"
            obs_dest.mkdir(parents=True, exist_ok=True)
            export_obsidian(G, obs_dest)
            print(f"[pocket_graph] wrote {obs_dest}/ ({G.number_of_nodes()} files)")
        except Exception as e:
            print(f"[pocket_graph] Obsidian export skipped: {e}")

    print(f"[pocket_graph] wrote {out_dir}/graph.json")
    print(f"[pocket_graph] wrote {out_dir}/GRAPH_REPORT.md")
    print(f"[pocket_graph] wrote {out_dir}/analysis.json")

    return {
        "graph": G,
        "communities": communities,
        "analysis": analysis,
        "out_dir": str(out_dir),
    }


__all__ = ["run", "supported_languages", "supported_extensions"]
