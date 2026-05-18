"""pocket-graph CLI.

Defaults are hardcoded -- no config file, no configure command.
Flags override defaults case-by-case.

Layout convention (cwd-relative):
    <cwd>/graph-out/      graph.json, graph.html, GRAPH_REPORT.md, cache/
    <cwd>/llm-wiki/       Obsidian wikilink files (when --export obsidian)

The Claude Code skill is installed globally under ~/.claude/skills/pocket-graph/
so every Claude Code session sees it without per-project setup.
"""
from __future__ import annotations
import argparse
import json
import os
import platform
import re
import sys
from pathlib import Path


# ============================================================
# Path helpers -- single source of truth for default locations
# Order: explicit flag > saved config > cwd-relative default
# ============================================================
def _rebuild_graph_html(out_dir: Path) -> None:
    """Regenerate derived artifacts after a graph mutation.

    Called after apply-semantic / apply-enrichments so the visualization
    and report reflect newly merged nodes/edges. Silent on per-artifact
    failure (graph.json is the source of truth; the rest is derived).

    Regenerates:
      - graph.html  (force-directed visualization)
      - GRAPH_REPORT.md  (god nodes, communities, surprises)
      - analysis.json  (community structure, god nodes raw data)
    """
    graph_path = out_dir / "graph.json"
    if not graph_path.exists():
        return

    try:
        from .build import from_node_link
        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
        G = from_node_link(graph_data)
    except Exception as e:
        print(f"[pocket-graph] WARNING: graph reload failed: {e}")
        return

    # graph.html
    try:
        from .export import export_html
        export_html(G, out_dir / "graph.html",
                     title=f"pocket-graph: {out_dir.parent.name}")
    except Exception as e:
        print(f"[pocket-graph] WARNING: graph.html regen failed: {e}")

    # GRAPH_REPORT.md + analysis.json
    try:
        from .cluster import cluster
        from .analyze import analyze
        from .report import render_report
        communities = cluster(G)
        analysis_data = analyze(G, communities)
        (out_dir / "GRAPH_REPORT.md").write_text(
            render_report(G, analysis_data), encoding="utf-8")
        (out_dir / "analysis.json").write_text(
            json.dumps(analysis_data, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"[pocket-graph] regenerated graph.html, GRAPH_REPORT.md, analysis.json")
    except Exception as e:
        print(f"[pocket-graph] WARNING: report regen failed: {e}")


def _invocation() -> str:
    """Return the user-facing command prefix that matches how the user invoked us.

    `python3 -m pocket_graph` if invoked as a module, otherwise `pocket-graph`.
    Used in user-facing "Next: ..." hints so messages match the user's environment.
    """
    # When invoked as `python3 -m pocket_graph`, sys.argv[0] ends with __main__.py
    if sys.argv and sys.argv[0].endswith("__main__.py"):
        return "python -m pocket_graph"
    return "pocket-graph"


def default_graph_dir(cwd: Path | None = None) -> Path:
    from . import config
    val = config.get("graph_out")
    if val:
        return Path(val).expanduser()
    return (cwd or Path.cwd()) / "graph-out"


def default_llm_wiki_dir(cwd: Path | None = None) -> Path:
    from . import config
    val = config.get("llm_wiki")
    if val:
        return Path(val).expanduser()
    return (cwd or Path.cwd()) / "llm-wiki"


def default_raw_dir(cwd: Path | None = None) -> Path:
    from . import config
    val = config.get("raw")
    if val:
        return Path(val).expanduser()
    return (cwd or Path.cwd()) / "raw"


def global_skill_dir() -> Path:
    """OS-aware global skill directory. Always global -- no override."""
    if platform.system() == "Windows":
        base = os.environ.get("USERPROFILE") or str(Path.home())
        return Path(base) / ".claude" / "skills" / "pocket-graph"
    return Path.home() / ".claude" / "skills" / "pocket-graph"


def _ensure_skill_installed(verbose: bool = True) -> bool:
    """Auto-install global skill on first run if missing.

    Idempotent: returns False if already installed (no message),
    True if just installed (prints first-run notice).
    """
    import shutil
    skill_dst_dir = global_skill_dir()
    skill_dst = skill_dst_dir / "SKILL.md"
    if skill_dst.exists():
        return False
    skill_src = Path(__file__).parent / "skill_assets" / "SKILL.md"
    if not skill_src.exists():
        # Bundled skill missing -- silently skip (don't crash data commands)
        return False
    skill_dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(skill_src, skill_dst)
    if verbose:
        print(f"[pocket-graph] first-run: installed Claude Code skill -> {skill_dst}")
    return True


# ============================================================
# Commands
# ============================================================
def cmd_configure(args):
    """Optionally save default paths so cwd doesn't matter.

    All keys are optional. Leave blank to use cwd-relative defaults.
    Saves to ~/.config/pocket-graph/config.json (or %APPDATA% on Windows).
    """
    from . import config

    cfg = config.load()
    print(f"Config file: {config.config_path()}")
    print()
    print("Leave blank to use cwd-relative default. Type '-' to clear an existing value.")
    print()

    def _ask(prompt: str, key: str, default_hint: str):
        current = cfg.get(key, "")
        suffix = f" [current: {current}]" if current else f" [default: {default_hint}]"
        try:
            ans = input(f"  {prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            sys.exit(0)
        if ans == "-":
            cfg.pop(key, None)
        elif ans:
            cfg[key] = str(Path(ans).expanduser().resolve())

    _ask("Graph output dir", "graph_out", "<cwd>/graph-out/")
    _ask("Obsidian wiki dir", "llm_wiki", "<cwd>/llm-wiki/")
    _ask("Raw corpus dir", "raw", "<cwd>/raw/")

    saved = config.save(cfg)
    print()
    print(f"Saved to {saved}")
    print()
    if cfg:
        print("Effective overrides:")
        for k, v in cfg.items():
            print(f"  {k}: {v}")
    else:
        print("No overrides -- all paths are cwd-relative defaults.")


def cmd_install(args):
    """Install Claude Code skill globally + build graph for current directory."""
    import shutil
    from . import run

    # 1. Install skill globally
    skill_src = Path(__file__).parent / "skill_assets" / "SKILL.md"
    if not skill_src.exists():
        print(f"Error: bundled SKILL.md not found at {skill_src}", file=sys.stderr)
        sys.exit(1)

    skill_dst_dir = global_skill_dir()
    skill_dst_dir.mkdir(parents=True, exist_ok=True)
    skill_dst = skill_dst_dir / "SKILL.md"
    shutil.copy2(skill_src, skill_dst)
    print(f"[pocket-graph] installed skill -> {skill_dst}")

    # 2. Initial build (unless --no-build)
    if args.no_build:
        print("[pocket-graph] skipped initial build (--no-build)")
        return

    source = Path(args.path).resolve() if args.path else Path.cwd()
    out_dir = Path(args.out) if args.out else default_graph_dir(source)

    print(f"[pocket-graph] building graph: {source} -> {out_dir}")
    run(source, out_dir=out_dir, export_formats=["html"])
    print(f"[pocket-graph] done. open {out_dir}/graph.html in a browser.")


def cmd_ingest(args):
    """Ingest a directory, URL, or keyword search into the graph."""
    _ensure_skill_installed()
    from . import run

    target = args.target
    cwd = Path.cwd()
    raw_dir = Path(args.raw).resolve() if args.raw else default_raw_dir(cwd)

    # Mode 1: directory
    if target and Path(target).expanduser().exists() and Path(target).expanduser().is_dir():
        source = Path(target).expanduser().resolve()
        # Output dir: explicit > source/graph-out (source-relative, not cwd-relative)
        out_dir = Path(args.out).resolve() if args.out else default_graph_dir(source)
        print(f"[pocket-graph] ingesting directory: {source} -> {out_dir}")
        run(source, out_dir=out_dir, export_formats=["html"])
        return

    # For URL/keyword/no-target modes, use cwd-based defaults
    out_dir = Path(args.out).resolve() if args.out else default_graph_dir(cwd)

    # For URL/keyword, raw_dir must exist
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Mode 2: URL
    if target and re.match(r"^https?://", target):
        from .fetcher import fetch_url
        try:
            saved = fetch_url(target, raw_dir)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[pocket-graph] saved {saved}")
        _update_or_build(raw_dir, out_dir)
        return

    # Mode 3: keyword
    if target:
        from .fetcher import search_and_fetch
        print(f"[pocket-graph] searching for {target!r}")
        try:
            saved = search_and_fetch(target, raw_dir, max_results=args.max_results)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        for p in saved:
            print(f"[pocket-graph] saved {p}")
        _update_or_build(raw_dir, out_dir)
        return

    # No target -> ingest current directory
    print(f"[pocket-graph] ingesting current directory: {cwd} -> {out_dir}")
    run(cwd, out_dir=out_dir, export_formats=["html"])


def _update_or_build(source: Path, out_dir: Path) -> None:
    """If the graph already exists, do incremental sync. Otherwise full build."""
    from . import run
    from .sync import update_graph
    if (out_dir / "graph.json").exists() and (out_dir / "manifest.json").exists():
        try:
            result = update_graph(source, out_dir=out_dir)
            print(f"[pocket-graph] sync: {result.get('action')}")
            return
        except Exception as e:
            print(f"[pocket-graph] sync failed, falling back to full build: {e}",
                   file=sys.stderr)
    run(source, out_dir=out_dir, export_formats=["html"])


def cmd_update(args):
    """Incremental re-ingest of an existing graph."""
    _ensure_skill_installed()
    from .sync import update_graph
    cwd = Path.cwd()
    source = Path(args.path).resolve() if args.path else cwd
    out_dir = Path(args.out).resolve() if args.out else default_graph_dir(cwd)
    result = update_graph(source, out_dir=out_dir)
    print(json.dumps(result, indent=2))


def cmd_query(args):
    """Query the graph.

    Default: BFS traversal -- returns subgraph as text within token budget.
    --list: just return ranked node list (legacy pocket-graph behavior).
    """
    _ensure_skill_installed()
    from .build import load_graph
    from . import query as pgq
    graph_path = Path(args.graph).resolve() if args.graph else default_graph_dir() / "graph.json"
    G = load_graph(graph_path)

    if args.list:
        # Legacy mode: ranked node list
        results = pgq.find_nodes(G, args.q, limit=args.limit)
        if not results:
            print(f"No matches for {args.q!r}.")
            return
        for r in results:
            score_str = f"score={r['score']}" if "score" in r else f"deg={r['degree']}"
            print(f"{r['id']:<40} {r['label']:<30} ({r['file_type']}) {score_str}")
        return

    # Default: BFS traversal
    text = pgq.query_graph_text(
        G, args.q,
        mode=args.mode, depth=args.depth,
        token_budget=args.budget,
        context_filters=args.context_filter,
    )
    print(text)


def cmd_check_update(args):
    """Cron-safe check: detect changed files since last graph build.

    Compares current corpus against `graph-out/manifest.json`. Reports new,
    changed, and deleted files. Always exits 0 so cron jobs don't alarm.
    Use `--exit-code` to make it exit 1 when changes are pending (for use
    in CI checks).
    """
    _ensure_skill_installed()
    from .sync import load_manifest, diff_corpus

    cwd = Path.cwd()
    source = Path(args.path).resolve() if args.path else cwd
    out_dir = Path(args.out).resolve() if args.out else default_graph_dir(cwd)

    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"[pocket-graph check-update] No manifest at {manifest_path}.")
        print(f"[pocket-graph check-update] Run `{_invocation()} ingest {source}` to create initial graph.")
        sys.exit(1 if args.exit_code else 0)

    diff = diff_corpus(source, out_dir)
    new = len(diff.get("new", []))
    changed = len(diff.get("changed", []))
    deleted = len(diff.get("deleted", []))
    confirmed = len(diff.get("confirmed", []))

    has_changes = (new + changed + deleted) > 0
    print(f"[pocket-graph check-update] {source}")
    print(f"  confirmed: {confirmed}")
    print(f"  new:       {new}")
    print(f"  changed:   {changed}")
    print(f"  deleted:   {deleted}")
    if has_changes:
        print(f"\n[pocket-graph check-update] Run `pocket-graph update` to apply.")
    else:
        print(f"\n[pocket-graph check-update] Graph is up to date.")

    if args.exit_code and has_changes:
        sys.exit(1)
    sys.exit(0)


def cmd_merge_graphs(args):
    """Merge multiple graph.json files into one cross-repo/cross-vault graph.

    Tagging: every node is tagged with `source_graph` set to the file's
    immediate parent directory name (e.g. graph-out -> "graph-out", or
    a project name if you've named output dirs accordingly).

    Conflict handling:
      - Node ID collision: merged. Existing fields kept, new fields added.
        `source_graph` becomes a sorted list of all graphs the node appeared in.
      - Edge dedup: keyed by (source, target, relation). Highest-confidence
        copy wins (EXTRACTED > INFERRED > AMBIGUOUS).
    """
    _ensure_skill_installed()
    from networkx.readwrite import json_graph

    if len(args.graphs) < 2:
        print("Error: provide at least 2 graph files to merge.", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out).resolve() if args.out else \
               default_graph_dir() / "merged-graph.json"

    confidence_rank = {"EXTRACTED": 3, "INFERRED": 2, "AMBIGUOUS": 1, None: 0, "": 0}

    merged_nodes: dict[str, dict] = {}
    # edge dedup: (source, target, relation) -> edge data
    merged_edges: dict[tuple, dict] = {}

    for gp_str in args.graphs:
        gp = Path(gp_str).resolve()
        if not gp.exists():
            print(f"Error: not found: {gp}", file=sys.stderr)
            sys.exit(1)
        data = json.loads(gp.read_text(encoding="utf-8"))
        # Use the parent directory's name as the source_graph tag
        # (e.g. /vaults/papers/graph-out/graph.json -> "papers")
        source_tag = gp.parent.parent.name if gp.parent.name == "graph-out" \
                     else gp.parent.name

        for node in data.get("nodes", []):
            nid = node["id"]
            if nid in merged_nodes:
                # ID collision -- merge fields without clobbering
                existing = merged_nodes[nid]
                existing_tags = existing.get("source_graph")
                if isinstance(existing_tags, str):
                    existing_tags = [existing_tags]
                elif not isinstance(existing_tags, list):
                    existing_tags = []
                if source_tag not in existing_tags:
                    existing_tags.append(source_tag)
                existing["source_graph"] = sorted(set(existing_tags))
                # Fill in any missing fields from the new node
                for key, val in node.items():
                    if key == "id":
                        continue
                    if key not in existing or not existing[key]:
                        existing[key] = val
                # Merge tags lists
                if "tags" in node and node["tags"]:
                    tag_set = set(existing.get("tags") or [])
                    tag_set.update(node["tags"])
                    existing["tags"] = sorted(tag_set)
            else:
                node_copy = dict(node)
                node_copy["source_graph"] = [source_tag]
                merged_nodes[nid] = node_copy

        edges_key = "links" if "links" in data else "edges"
        for edge in data.get(edges_key, []):
            triple = (edge.get("source"), edge.get("target"), edge.get("relation"))
            if None in triple[:2]:
                continue
            existing = merged_edges.get(triple)
            if existing is None:
                merged_edges[triple] = dict(edge)
            else:
                # Keep the higher-confidence edge
                new_rank = confidence_rank.get(edge.get("confidence"), 0)
                old_rank = confidence_rank.get(existing.get("confidence"), 0)
                if new_rank > old_rank:
                    merged_edges[triple] = dict(edge)

    # Build output in NetworkX node-link format
    # Use the same edges_key as the first input graph for compatibility
    first_data = json.loads(Path(args.graphs[0]).read_text(encoding="utf-8"))
    out_edges_key = "links" if "links" in first_data else "edges"

    out = {
        "directed": first_data.get("directed", True),
        "multigraph": first_data.get("multigraph", False),
        "graph": {},
        "nodes": list(merged_nodes.values()),
        out_edges_key: list(merged_edges.values()),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    print(f"[pocket-graph] merged {len(args.graphs)} graphs:")
    print(f"  total nodes: {len(merged_nodes)}")
    print(f"  total edges: {len(merged_edges)}")
    print(f"  written to:  {out_path}")


def cmd_watch(args):
    """Watch a folder and auto-rebuild the graph on file changes.

    Code-only changes trigger immediate AST re-extraction (no LLM).
    Non-code changes (PDF, markdown, image) write a `needs_update` flag and
    notify the user to run `pocket-graph update` (or /pocket-graph in
    Claude Code) for LLM-backed semantic re-extraction.
    """
    _ensure_skill_installed()
    from .watch import watch

    cwd = Path.cwd()
    watch_path = Path(args.path).resolve() if args.path else cwd
    out_dir = Path(args.out).resolve() if args.out else default_graph_dir(cwd)

    if not watch_path.exists():
        print(f"Error: path not found: {watch_path}", file=sys.stderr)
        sys.exit(1)

    watch(watch_path, out_dir, debounce=args.debounce)


def cmd_tree(args):
    """Emit a D3 v7 collapsible-tree HTML view of graph.json.

    A self-contained printable / browseable tree-of-modules view that
    complements the force-directed graph.html. Useful for inspecting a code
    repo's directory hierarchy and locating symbols by file.
    """
    _ensure_skill_installed()
    from .tree import write_tree_html, DEFAULT_MAX_CHILDREN

    cwd = Path.cwd()
    graph_path = Path(args.graph).resolve() if args.graph else \
                 default_graph_dir(cwd) / "graph.json"
    if not graph_path.exists():
        print(f"Error: graph.json not found: {graph_path}", file=sys.stderr)
        print(f"       run `{_invocation()} ingest <path>` first.", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output).resolve() if args.output else \
                  default_graph_dir(cwd) / "tree.html"

    write_tree_html(
        graph_path, output_path,
        root=args.root,
        max_children=args.max_children if args.max_children else DEFAULT_MAX_CHILDREN,
        project_label=args.label,
    )
    print(f"[pocket-graph] wrote {output_path}")


def cmd_clone(args):
    """Clone a GitHub repo for ingest. Returns local path.

    Clones into ~/.pocket-graph/repos/<owner>/<repo> by default so repeated
    runs on the same URL reuse the existing clone (git pull).
    """
    _ensure_skill_installed()
    import subprocess
    url = args.url.rstrip("/")
    if url.endswith(".git"):
        git_url = url
        url = url[:-4]
    else:
        git_url = url + ".git"

    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        print(f"error: not a recognised GitHub URL: {url}", file=sys.stderr)
        sys.exit(1)
    owner, repo = m.group(1), m.group(2)

    if args.out:
        dest = Path(args.out).resolve()
    else:
        dest = Path.home() / ".pocket-graph" / "repos" / owner / repo

    if args.branch and args.branch.startswith("-"):
        print(f"error: invalid branch name: {args.branch!r}", file=sys.stderr)
        sys.exit(1)

    if dest.exists():
        print(f"Repo already cloned at {dest} -- pulling latest...", flush=True)
        cmd = ["git", "-C", str(dest), "pull"]
        if args.branch:
            cmd += ["origin", "--", args.branch]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"warning: git pull failed:\n{result.stderr}", file=sys.stderr)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"Cloning {url} -> {dest} ...", flush=True)
        cmd = ["git", "clone", "--depth", "1"]
        if args.branch:
            cmd += ["--branch", args.branch]
        cmd += ["--", git_url, str(dest)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"error: git clone failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)

    print(f"Ready at: {dest}", flush=True)
    print(f"\nNext: {_invocation()} ingest {dest}")


def cmd_explain(args):
    """Explain a node: ID, source, type, community, degree, neighbors with relations.

    Returns a deterministic plain-text description of a node and its
    immediate connections. Useful as a quick inspection without running
    a full BFS query.
    """
    _ensure_skill_installed()
    from .build import load_graph
    from . import query as pgq
    graph_path = Path(args.graph).resolve() if args.graph else default_graph_dir() / "graph.json"
    G = load_graph(graph_path)

    # Find by label or ID (case-insensitive substring + diacritic-insensitive)
    target = args.node
    target_norm = pgq._strip_diacritics(target).lower()
    matches = []
    for nid, data in G.nodes(data=True):
        nid_lower = nid.lower()
        label_norm = pgq._strip_diacritics(data.get("label") or "").lower()
        if target_norm == nid_lower or target_norm == label_norm:
            matches.insert(0, nid)  # exact match first
        elif target_norm in nid_lower or target_norm in label_norm:
            matches.append(nid)

    if not matches:
        print(f"No node matching {args.node!r} found.")
        sys.exit(1)

    if len(matches) > 1 and not args.first:
        print(f"Multiple matches for {args.node!r}:")
        for nid in matches[:10]:
            data = G.nodes[nid]
            print(f"  {nid:<40} {data.get('label', '')}")
        if len(matches) > 10:
            print(f"  ... and {len(matches) - 10} more")
        print(f"\nUse --first to explain the top match, or specify a more exact id/label.")
        return

    nid = matches[0]
    d = G.nodes[nid]
    print(f"Node: {d.get('label', nid)}")
    print(f"  ID:           {nid}")
    src = d.get("source_file", "")
    loc = d.get("source_location", "")
    print(f"  Source:       {src} {loc}".rstrip())
    print(f"  Type:         {d.get('file_type', '')}")
    if d.get("community"):
        print(f"  Community:    {d.get('community')}")
    print(f"  Degree:       {G.degree(nid)}")

    if d.get("description"):
        print(f"  Description:  {d['description']}")
    if d.get("tags"):
        print(f"  Tags:         {', '.join(d['tags'])}")

    neighbors = list(G.neighbors(nid))
    if neighbors:
        print(f"\nConnections ({len(neighbors)}):")
        for nb in sorted(neighbors, key=lambda n: G.degree(n), reverse=True)[:args.limit]:
            edge_data = G.edges[nid, nb]
            # MultiGraph compatibility
            if isinstance(edge_data, dict) and "relation" not in edge_data:
                edge_data = next(iter(edge_data.values()), {})
            rel = edge_data.get("relation", "")
            conf = edge_data.get("confidence", "")
            nb_label = G.nodes[nb].get("label", nb)
            print(f"  --[{rel}, {conf}]--> {nb_label}")
        if len(neighbors) > args.limit:
            print(f"  ... and {len(neighbors) - args.limit} more")


def cmd_path(args):
    """Find shortest path between two nodes."""
    _ensure_skill_installed()
    from .build import load_graph
    from . import query as pgq
    graph_path = Path(args.graph).resolve() if args.graph else default_graph_dir() / "graph.json"
    G = load_graph(graph_path)
    result = pgq.shortest_path(G, args.source, args.target)
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print(f"Path length: {result['length']}")
    for i, n in enumerate(result["nodes"]):
        edge = ""
        if i < len(result["edges"]):
            e = result["edges"][i]
            edge = f"  --[{e['relation']}, {e['confidence']}]-->"
        print(f"  {n['label']}{edge}")


def cmd_export(args):
    """Re-export an existing graph to additional formats."""
    _ensure_skill_installed()
    from .build import load_graph
    from .export import export_html, export_graphml, export_neo4j_cypher, export_obsidian

    graph_path = Path(args.graph).resolve() if args.graph else default_graph_dir() / "graph.json"
    G = load_graph(graph_path)
    out_dir = graph_path.parent
    cwd = Path.cwd()

    formats = args.formats.split(",")
    for fmt in (f.strip() for f in formats):
        if fmt == "html":
            export_html(G, out_dir / "graph.html", title=f"pocket-graph: {out_dir.name}")
            print(f"wrote {out_dir}/graph.html")
        elif fmt == "graphml":
            export_graphml(G, out_dir / "graph.graphml")
            print(f"wrote {out_dir}/graph.graphml")
        elif fmt == "cypher":
            export_neo4j_cypher(G, out_dir / "cypher.txt")
            print(f"wrote {out_dir}/cypher.txt")
        elif fmt == "obsidian":
            obs_dir = Path(args.obsidian_dir).resolve() if args.obsidian_dir \
                       else default_llm_wiki_dir(cwd)
            obs_dir.mkdir(parents=True, exist_ok=True)
            export_obsidian(G, obs_dir)
            print(f"wrote {obs_dir}/  ({G.number_of_nodes()} files)")
        else:
            print(f"unknown format: {fmt!r}", file=sys.stderr)


def cmd_serve(args):
    """Run MCP server over stdio."""
    from .serve import serve
    graph_path = Path(args.graph).resolve() if args.graph else default_graph_dir() / "graph.json"
    serve(graph_path)


def cmd_stats(args):
    """Show supported languages."""
    from .languages import EXTENSION_REGISTRY
    by_lang: dict[str, list[str]] = {}
    for ext, cfg in EXTENSION_REGISTRY.items():
        by_lang.setdefault(cfg.name, []).append(ext)
    print(f"Supported languages: {len(by_lang)}")
    for name, exts in sorted(by_lang.items()):
        print(f"  {name:<14} {' '.join(sorted(exts))}")


def cmd_init_vault(args):
    """Create pocket-graph vault scaffolding (raw/, LLM Wiki/_meta/, etc)."""
    _ensure_skill_installed()
    from .init_vault import init_vault
    root = Path(args.path).resolve() if args.path else Path.cwd()
    print(f"[pocket-graph] initializing vault at {root}")
    init_vault(root, verbose=True)
    print()
    print("Next steps:")
    print(f"  cd {root}")
    print(f"  {_invocation()} ingest <url-or-path>   # add your first source")


def cmd_fetch(args):
    """Fetch a URL into raw/ without building a graph.

    For use by the SKILL -- separates fetching from graph building so the LLM
    can read the raw and build graph + wiki in a single pass.
    """
    _ensure_skill_installed()
    from .fetcher import fetch_url
    cwd = Path.cwd()
    raw_dir = Path(args.raw).resolve() if args.raw else cwd / "raw" / "crawled"
    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        saved = fetch_url(args.url, raw_dir)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[pocket-graph] saved {saved}")


def cmd_skeleton(args):
    """Build a graph skeleton from raw/ -- tree-sitter only, no LLM enrichment.

    Produces graph-out/graph.json with node/edge structure but empty
    descriptions. Descriptions are filled later by `apply-enrichments`.
    """
    _ensure_skill_installed()
    from . import run
    cwd = Path.cwd()
    source = Path(args.path).resolve() if args.path else cwd
    out_dir = Path(args.out).resolve() if args.out else default_graph_dir(cwd)
    print(f"[pocket-graph] building graph skeleton: {source} -> {out_dir}")
    run(source, out_dir=out_dir, export_formats=["html"])
    print(f"[pocket-graph] skeleton ready. fill enrichments via SKILL,")
    print(f"               then run: {_invocation()} apply-enrichments <file>")


def cmd_apply_enrichments(args):
    """Merge LLM-generated enrichments into graph.json.

    Input format (enrichments.json):
        {
          "<node_id>": {
            "description": "...",
            "tags": ["..."]
          },
          ...
        }
    """
    _ensure_skill_installed()
    enrich_path = Path(args.file).resolve()
    if not enrich_path.exists():
        print(f"Error: enrichments file not found: {enrich_path}", file=sys.stderr)
        sys.exit(1)

    cwd = Path.cwd()
    graph_path = Path(args.graph).resolve() if args.graph else \
                 default_graph_dir(cwd) / "graph.json"
    if not graph_path.exists():
        print(f"Error: graph.json not found: {graph_path}", file=sys.stderr)
        print(f"       run `{_invocation()} skeleton` first.", file=sys.stderr)
        sys.exit(1)

    enrichments = json.loads(enrich_path.read_text(encoding="utf-8"))
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}

    applied = 0
    skipped_missing = 0
    for node_id, enrich in enrichments.items():
        if node_id not in nodes_by_id:
            skipped_missing += 1
            continue
        node = nodes_by_id[node_id]
        if "description" in enrich and enrich["description"]:
            node["description"] = enrich["description"]
        if "tags" in enrich and enrich["tags"]:
            existing = set(node.get("tags", []) or [])
            existing.update(enrich["tags"])
            node["tags"] = sorted(existing)
        applied += 1

    graph_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"[pocket-graph] applied {applied} enrichments to {graph_path.name}")
    if skipped_missing:
        print(f"[pocket-graph] skipped {skipped_missing} entries (node_id not in graph)")

    # Regenerate graph.html with the enriched data
    _rebuild_graph_html(graph_path.parent)


def cmd_apply_semantic(args):
    """Merge LLM-extracted semantic nodes/edges into graph.json.

    For PDFs, images, and prose where tree-sitter can't reach. The SKILL
    extracts entity-level nodes and relations in a single LLM pass (using
    the documented JSON schema) and feeds them here.

    Input format (semantic.json):
        {
          "nodes": [
            {"id": "stem_entity", "label": "...", "file_type": "concept|paper|image",
             "source_file": "raw/...", "description": "..." (optional),
             "tags": [...] (optional), ...}
          ],
          "edges": [
            {"source": "node_id", "target": "node_id",
             "relation": "calls|implements|references|cites|conceptually_related_to|shares_data_with|semantically_similar_to",
             "confidence": "EXTRACTED|INFERRED|AMBIGUOUS",
             "source_file": "raw/..." (optional)}
          ]
        }

    Behavior:
        - Nodes: merged by id. New nodes added; existing nodes have description/tags
          updated only if not already set.
        - Edges: merged by (source, target, relation) triple. Duplicates skipped.
        - Existing tree-sitter nodes/edges are preserved untouched.
    """
    _ensure_skill_installed()
    sem_path = Path(args.file).resolve()
    if not sem_path.exists():
        print(f"Error: semantic file not found: {sem_path}", file=sys.stderr)
        sys.exit(1)

    cwd = Path.cwd()
    graph_path = Path(args.graph).resolve() if args.graph else \
                 default_graph_dir(cwd) / "graph.json"
    if not graph_path.exists():
        print(f"Error: graph.json not found: {graph_path}", file=sys.stderr)
        print(f"       run `{_invocation()} skeleton` first.", file=sys.stderr)
        sys.exit(1)

    semantic = json.loads(sem_path.read_text(encoding="utf-8"))
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    # === Merge nodes ===
    existing_node_ids = {n["id"] for n in graph.get("nodes", [])}
    nodes_added = 0
    nodes_enriched = 0
    nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}

    for new_node in semantic.get("nodes", []):
        nid = new_node.get("id")
        if not nid:
            continue
        if nid in existing_node_ids:
            # Update fields without clobbering tree-sitter info
            existing = nodes_by_id[nid]
            if "description" in new_node and new_node["description"] \
                    and not existing.get("description"):
                existing["description"] = new_node["description"]
            if "tags" in new_node and new_node["tags"]:
                tags = set(existing.get("tags") or [])
                tags.update(new_node["tags"])
                existing["tags"] = sorted(tags)
            nodes_enriched += 1
        else:
            graph.setdefault("nodes", []).append(new_node)
            existing_node_ids.add(nid)
            nodes_added += 1

    # === Merge edges (NetworkX node-link uses 'links' or 'edges') ===
    edges_key = "links" if "links" in graph else "edges"
    if edges_key not in graph:
        graph[edges_key] = []

    existing_edges = {(e.get("source"), e.get("target"), e.get("relation"))
                       for e in graph[edges_key]}
    edges_added = 0
    edges_skipped = 0

    for new_edge in semantic.get("edges", []):
        triple = (new_edge.get("source"), new_edge.get("target"),
                  new_edge.get("relation"))
        if None in triple:
            continue
        # Skip if either endpoint isn't in the graph
        if triple[0] not in existing_node_ids or triple[1] not in existing_node_ids:
            edges_skipped += 1
            continue
        if triple in existing_edges:
            continue
        graph[edges_key].append(new_edge)
        existing_edges.add(triple)
        edges_added += 1

    graph_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"[pocket-graph] semantic merge into {graph_path.name}:")
    print(f"  nodes added:    {nodes_added}")
    print(f"  nodes enriched: {nodes_enriched}")
    print(f"  edges added:    {edges_added}")
    if edges_skipped:
        print(f"  edges skipped:  {edges_skipped} (endpoint not in graph)")

    # Regenerate graph.html so the visualization reflects the new nodes/edges.
    # (graph.json is the source of truth; graph.html is a derived artifact.)
    _rebuild_graph_html(graph_path.parent)


# ============================================================
# Argparse
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pocket-graph",
        description="Knowledge graph builder for personal corpora — code, docs, papers in one queryable graph",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # install
    sp_install = sub.add_parser("install",
        help="Install Claude Code skill globally + run initial graph build")
    sp_install.add_argument("path", nargs="?", default=None,
        help="Project to build initial graph for (default: cwd)")
    sp_install.add_argument("--out",
        help="Override graph output directory (default: <cwd>/graph-out/)")
    sp_install.add_argument("--no-build", action="store_true",
        help="Only install the skill, skip initial build")
    sp_install.set_defaults(fn=cmd_install)

    # configure (optional)
    sp_configure = sub.add_parser("configure",
        help="Optional: save default paths to override cwd-relative defaults")
    sp_configure.set_defaults(fn=cmd_configure)

    # ingest
    sp_ingest = sub.add_parser("ingest",
        help="Ingest a directory, URL, or keyword search into the graph")
    sp_ingest.add_argument("target", nargs="?", default=None,
        help="Directory path, URL, or search keyword. Omit to ingest cwd.")
    sp_ingest.add_argument("--raw",
        help="Raw corpus directory for URL/keyword fetches (default: <cwd>/raw/)")
    sp_ingest.add_argument("--out",
        help="Graph output directory (default: <cwd>/graph-out/)")
    sp_ingest.add_argument("--max-results", type=int, default=3,
        help="Max search results to fetch in keyword mode (default: 3)")
    sp_ingest.set_defaults(fn=cmd_ingest)

    # update
    sp_update = sub.add_parser("update", help="Incremental re-ingest")
    sp_update.add_argument("path", nargs="?", default=None,
        help="Source path (default: cwd)")
    sp_update.add_argument("--out",
        help="Graph output directory (default: <cwd>/graph-out/)")
    sp_update.set_defaults(fn=cmd_update)

    # query
    sp_query = sub.add_parser("query",
        help="Query the graph: BFS traversal text by default, or --list for ranked node list")
    sp_query.add_argument("q", help="Search query")
    sp_query.add_argument("--graph",
        help="Path to graph.json (default: <cwd>/graph-out/graph.json)")
    sp_query.add_argument("--list", action="store_true",
        help="Return ranked node list instead of BFS subgraph (legacy mode)")
    sp_query.add_argument("--limit", type=int, default=10,
        help="Max nodes in --list mode (default 10)")
    sp_query.add_argument("--mode", choices=["bfs", "dfs"], default="bfs",
        help="Traversal mode (default: bfs)")
    sp_query.add_argument("--depth", type=int, default=3,
        help="Traversal depth (default 3)")
    sp_query.add_argument("--budget", type=int, default=2000,
        help="Token budget for output (default 2000)")
    sp_query.add_argument("--context-filter", action="append",
        help="Edge-context filter, e.g. --context-filter call --context-filter import")
    sp_query.set_defaults(fn=cmd_query)

    # path
    sp_path = sub.add_parser("path", help="Shortest path between two nodes")
    sp_path.add_argument("source")
    sp_path.add_argument("target")
    sp_path.add_argument("--graph",
        help="Path to graph.json (default: <cwd>/graph-out/graph.json)")
    sp_path.set_defaults(fn=cmd_path)

    # explain
    sp_explain = sub.add_parser("explain",
        help="Show a node's details and immediate neighbors with relations")
    sp_explain.add_argument("node",
        help="Node ID or label (case- and diacritic-insensitive substring match)")
    sp_explain.add_argument("--graph",
        help="Path to graph.json (default: <cwd>/graph-out/graph.json)")
    sp_explain.add_argument("--limit", type=int, default=20,
        help="Max neighbors to show (default 20)")
    sp_explain.add_argument("--first", action="store_true",
        help="If multiple matches, take the first one without listing")
    sp_explain.set_defaults(fn=cmd_explain)

    # clone
    sp_clone = sub.add_parser("clone",
        help="Clone a GitHub repo for ingest. Reuses ~/.pocket-graph/repos/<owner>/<repo> across runs.")
    sp_clone.add_argument("url", help="GitHub URL (https or ssh)")
    sp_clone.add_argument("--branch", help="Specific branch to clone")
    sp_clone.add_argument("--out", help="Custom output directory")
    sp_clone.set_defaults(fn=cmd_clone)

    # check-update
    sp_chk = sub.add_parser("check-update",
        help="Cron-safe check for pending corpus changes (compares against manifest)")
    sp_chk.add_argument("path", nargs="?", default=None,
        help="Corpus path (default: cwd)")
    sp_chk.add_argument("--out",
        help="Graph output directory (default: <cwd>/graph-out/)")
    sp_chk.add_argument("--exit-code", action="store_true",
        help="Exit 1 when changes are pending (for CI checks). Default: always exit 0.")
    sp_chk.set_defaults(fn=cmd_check_update)

    # merge-graphs
    sp_merge = sub.add_parser("merge-graphs",
        help="Merge multiple graph.json files into one cross-repo graph")
    sp_merge.add_argument("graphs", nargs="+",
        help="Two or more graph.json paths to merge")
    sp_merge.add_argument("--out",
        help="Output path (default: <cwd>/graph-out/merged-graph.json)")
    sp_merge.set_defaults(fn=cmd_merge_graphs)

    # tree
    sp_tree = sub.add_parser("tree",
        help="Emit a D3 v7 collapsible-tree HTML view of graph.json")
    sp_tree.add_argument("--graph",
        help="Path to graph.json (default: <cwd>/graph-out/graph.json)")
    sp_tree.add_argument("--output",
        help="Output HTML path (default: <cwd>/graph-out/tree.html)")
    sp_tree.add_argument("--root",
        help="Treat this path as the tree root (default: auto-detect common ancestor)")
    sp_tree.add_argument("--max-children", type=int, default=0,
        help="Cap children per node (default 200; 0 = use module default)")
    sp_tree.add_argument("--label",
        help="Label for the root node (default: directory name)")
    sp_tree.set_defaults(fn=cmd_tree)

    # watch
    sp_watch = sub.add_parser("watch",
        help="Watch a folder and auto-rebuild the graph on file changes (requires watchdog)")
    sp_watch.add_argument("path", nargs="?", default=None,
        help="Folder to watch (default: cwd)")
    sp_watch.add_argument("--out",
        help="Graph output directory (default: <cwd>/graph-out/)")
    sp_watch.add_argument("--debounce", type=float, default=3.0,
        help="Seconds to wait after the last change before rebuild (default 3)")
    sp_watch.set_defaults(fn=cmd_watch)

    # export
    sp_export = sub.add_parser("export",
        help="Re-export an existing graph to additional formats")
    sp_export.add_argument("--graph",
        help="Path to graph.json (default: <cwd>/graph-out/graph.json)")
    sp_export.add_argument("--formats", default="obsidian",
        help="Comma-separated: html, graphml, cypher, obsidian (default: obsidian)")
    sp_export.add_argument("--obsidian-dir",
        help="Obsidian export directory (default: <cwd>/llm-wiki/)")
    sp_export.set_defaults(fn=cmd_export)

    # serve
    sp_serve = sub.add_parser("serve", help="Run MCP server over stdio")
    sp_serve.add_argument("graph", nargs="?", default=None,
        help="Path to graph.json (default: <cwd>/graph-out/graph.json)")
    sp_serve.set_defaults(fn=cmd_serve)

    # stats
    sp_stats = sub.add_parser("stats", help="Show supported languages")
    sp_stats.set_defaults(fn=cmd_stats)

    # === SKILL helpers -- used by the /pocket-graph slash command ===

    sp_init = sub.add_parser("init-vault",
        help="Create pocket-graph vault scaffolding (raw/, LLM Wiki/_meta/)")
    sp_init.add_argument("path", nargs="?", default=None,
        help="Vault root (default: cwd)")
    sp_init.set_defaults(fn=cmd_init_vault)

    sp_fetch = sub.add_parser("fetch",
        help="Fetch a URL into raw/ without building graph (for SKILL use)")
    sp_fetch.add_argument("url")
    sp_fetch.add_argument("--raw",
        help="Raw destination dir (default: <cwd>/raw/crawled/)")
    sp_fetch.set_defaults(fn=cmd_fetch)

    sp_skel = sub.add_parser("skeleton",
        help="Build graph skeleton (tree-sitter only, no LLM enrichment)")
    sp_skel.add_argument("path", nargs="?", default=None,
        help="Source path (default: cwd)")
    sp_skel.add_argument("--out",
        help="Graph output directory (default: <cwd>/graph-out/)")
    sp_skel.set_defaults(fn=cmd_skeleton)

    sp_apply = sub.add_parser("apply-enrichments",
        help="Merge SKILL-generated enrichments into graph.json")
    sp_apply.add_argument("file", help="Path to enrichments.json")
    sp_apply.add_argument("--graph",
        help="Path to graph.json (default: <cwd>/graph-out/graph.json)")
    sp_apply.set_defaults(fn=cmd_apply_enrichments)

    sp_sem = sub.add_parser("apply-semantic",
        help="Merge SKILL-extracted semantic nodes/edges into the graph")
    sp_sem.add_argument("file", help="Path to semantic.json {nodes:[...], edges:[...]}")
    sp_sem.add_argument("--graph",
        help="Path to graph.json (default: <cwd>/graph-out/graph.json)")
    sp_sem.set_defaults(fn=cmd_apply_semantic)

    return p


def main():
    # Windows console default is cp949 (Korean), cp1252 (Western), etc.
    # Force UTF-8 so any output works regardless of system locale.
    # Best-effort — fails silently if streams don't support reconfigure
    # (older Python, redirected pipes on some platforms, etc).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = build_parser()
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
