"""Re-ingest sync: detect changed files via SHA256 manifest, prune deleted nodes,
re-extract changed files only.

This addresses the core "wiki/raw sync guarantee" concern: when source files
change, the graph stays consistent. Three states are tracked per file:

  confirmed: file unchanged since last build, cached extraction reused
  new:       file added or changed since last build, re-extracted
  stale:     file deleted from disk but nodes still in graph (pruned)
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .detect import collect_files, classify, FileType
from .extract import extract
from .build import build, save_graph, load_graph


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _manifest_path(out_dir: Path) -> Path:
    return out_dir / "manifest.json"


def load_manifest(out_dir: Path) -> dict:
    """Load the file manifest (path -> hash + last_seen)."""
    p = _manifest_path(out_dir)
    if not p.exists():
        return {"files": {}, "last_build": None}
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(out_dir: Path, manifest: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(out_dir).write_text(json.dumps(manifest, indent=2),
                                         encoding="utf-8")


def diff_corpus(root: Path, out_dir: Path) -> dict:
    """Detect file-level changes since last manifest.

    Returns dict with 4 keys:
      confirmed: paths whose hash matches manifest
      changed:   paths whose hash differs from manifest (modified)
      new:       paths in corpus but not in manifest
      deleted:   paths in manifest but not in corpus (file was removed)
    """
    manifest = load_manifest(out_dir)
    old_files: dict[str, str] = manifest.get("files", {})

    current_files = collect_files(root)
    current_paths = {str(p): p for p in current_files}

    confirmed, changed, new, deleted = [], [], [], []

    for path_str, p in current_paths.items():
        h = _file_hash(p)
        if path_str in old_files:
            if old_files[path_str]["hash"] == h:
                confirmed.append(p)
            else:
                changed.append(p)
        else:
            new.append(p)

    for path_str in old_files:
        if path_str not in current_paths:
            deleted.append(path_str)

    return {
        "confirmed": confirmed,
        "changed": changed,
        "new": new,
        "deleted": deleted,
    }


def update_graph(root: Path, out_dir: Path | None = None,
                 directed: bool = True) -> dict:
    """Incremental re-ingest: re-extract only new/changed files; prune deleted.

    Returns a summary dict with counts.
    """
    root = Path(root).resolve()
    if out_dir is None:
        out_dir = root / "graph-out"
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    diff = diff_corpus(root, out_dir)

    print(f"[pocket_graph.sync] confirmed: {len(diff['confirmed'])}, "
          f"changed: {len(diff['changed'])}, "
          f"new: {len(diff['new'])}, "
          f"deleted: {len(diff['deleted'])}")

    # If first run (no manifest): full build
    manifest = load_manifest(out_dir)
    if manifest.get("last_build") is None:
        print("[pocket_graph.sync] no prior manifest -- full build")
        from . import run as full_run
        full_run(root, out_dir=out_dir, directed=directed)
        # Update manifest now
        new_manifest = {"files": {}, "last_build": datetime.now(timezone.utc).isoformat()}
        for p in diff["confirmed"] + diff["changed"] + diff["new"]:
            new_manifest["files"][str(p)] = {
                "hash": _file_hash(p),
                "last_seen": new_manifest["last_build"],
            }
        save_manifest(out_dir, new_manifest)
        return {**{k: len(v) for k, v in diff.items()}, "action": "full_build"}

    # Incremental: re-extract only changed/new files
    files_to_extract = diff["changed"] + diff["new"]
    files_to_extract_classified = [
        f for f in files_to_extract
        if classify(f) in (FileType.CODE, FileType.DOCUMENT, FileType.PAPER)
    ]

    if not files_to_extract_classified and not diff["deleted"]:
        print("[pocket_graph.sync] no changes -- graph is up to date")
        return {**{k: len(v) for k, v in diff.items()}, "action": "no_op"}

    # Run extraction on changed files only (cache invalidated for them)
    new_extractions = []
    if files_to_extract_classified:
        # Invalidate cache entries for changed files
        for f in diff["changed"]:
            cache_p = out_dir / "cache" / f"{_file_hash(f)}.json"
            if cache_p.exists():
                cache_p.unlink()
        # Load cached extractions for confirmed files so Pass 2 sees the
        # full corpus (otherwise cross-file calls into unchanged files get
        # dropped as unresolved). The cached extractions are Pass-1 results
        # -- Pass 2 will re-resolve everything together.
        confirmed_classified = [
            f for f in diff["confirmed"]
            if classify(f) in (FileType.CODE, FileType.DOCUMENT, FileType.PAPER)
        ]
        all_files = files_to_extract_classified + confirmed_classified
        new_extractions = extract(all_files, cache_root=out_dir)
        print(f"[pocket_graph.sync] re-extracted {len(files_to_extract_classified)} files "
              f"(plus {len(confirmed_classified)} from cache for Pass 2)")

    # Load existing graph and merge
    graph_path = out_dir / "graph.json"
    if graph_path.exists():
        G = load_graph(graph_path)
        # Convert existing graph to extraction-dict form so we can rebuild
        existing_nodes = [{"id": n, **G.nodes[n]} for n in G.nodes]
        existing_edges = [{"source": u, "target": v, **d}
                          for u, v, d in G.edges(data=True)]
        # Drop nodes from changed/deleted source files (will be re-added if still extracted).
        # Also drop nodes from confirmed files because we re-loaded them via the cache
        # for Pass 2; otherwise they'd appear twice.
        invalidated_files = (set(str(f) for f in diff["changed"])
                              | set(diff["deleted"])
                              | set(str(f) for f in diff["confirmed"]))
        existing_nodes = [n for n in existing_nodes
                           if n.get("source_file", "") not in invalidated_files]
        existing_edges = [e for e in existing_edges
                           if e.get("source_file", "") not in invalidated_files]
        base_extraction = [{"nodes": existing_nodes, "edges": existing_edges}]
    else:
        base_extraction = []

    G = build(base_extraction + new_extractions, directed=directed)

    # Update analysis + report + html
    from .cluster import cluster
    from .analyze import analyze
    from .report import render_report
    from .build import attach_hyperedges
    communities = cluster(G)
    attach_hyperedges(G, communities)
    analysis_data = analyze(G, communities)
    save_graph(G, graph_path)
    (out_dir / "GRAPH_REPORT.md").write_text(render_report(G, analysis_data), encoding="utf-8")
    (out_dir / "analysis.json").write_text(
        json.dumps(analysis_data, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        from .export import export_html
        export_html(G, out_dir / "graph.html",
                     title=f"pocket-graph: {root.name}")
    except Exception:
        pass

    # Update manifest
    new_manifest = {"files": {}, "last_build": datetime.now(timezone.utc).isoformat()}
    for p in diff["confirmed"] + diff["changed"] + diff["new"]:
        new_manifest["files"][str(p)] = {
            "hash": _file_hash(p),
            "last_seen": new_manifest["last_build"],
        }
    save_manifest(out_dir, new_manifest)

    return {
        **{k: len(v) for k, v in diff.items()},
        "action": "incremental",
        "graph_nodes_after": G.number_of_nodes(),
        "graph_edges_after": G.number_of_edges(),
    }


__all__ = ["diff_corpus", "update_graph", "load_manifest", "save_manifest"]
