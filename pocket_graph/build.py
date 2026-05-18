"""Build a NetworkX graph from extraction results.

Handles 'edges' or 'links' compatibility, deduplication, and node/edge
merging across files.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
import networkx as nx

from .validate import validate_extraction


def _normalize_id(s: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    return cleaned.strip("_").lower()


def _norm_source_file(p: str | None) -> str | None:
    return p.replace("\\", "/") if p else p


def build_from_json(extraction: dict, *, directed: bool = True) -> nx.Graph:
    """Assemble nodes/edges into a NetworkX (Di)Graph."""
    if "edges" not in extraction and "links" in extraction:
        extraction = dict(extraction, edges=extraction["links"])

    errors = validate_extraction(extraction)
    real_errors = [e for e in errors if "does not match any node id" not in e]
    if real_errors:
        print(f"[pocket_graph] Schema warning ({len(real_errors)}): {real_errors[0]}",
              file=sys.stderr)

    G: nx.Graph = nx.DiGraph() if directed else nx.Graph()

    for node in extraction.get("nodes", []):
        if "source_file" in node:
            node["source_file"] = _norm_source_file(node["source_file"])
        G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})

    node_set = set(G.nodes())
    norm_to_id = {_normalize_id(nid): nid for nid in node_set}

    for edge in extraction.get("edges", []):
        src, tgt = edge.get("source"), edge.get("target")
        if src is None or tgt is None:
            continue
        if src not in node_set:
            src = norm_to_id.get(_normalize_id(src), src)
        if tgt not in node_set:
            tgt = norm_to_id.get(_normalize_id(tgt), tgt)
        if src not in node_set or tgt not in node_set:
            continue  # dangling edge
        attrs = {k: v for k, v in edge.items() if k not in ("source", "target")}
        if "source_file" in attrs:
            attrs["source_file"] = _norm_source_file(attrs["source_file"])
        attrs["_src"] = src
        attrs["_tgt"] = tgt
        # Weight: number of times this (src, tgt, relation) edge was extracted.
        # Used by community detection and by callees/callers to surface hot calls.
        relation = attrs.get("relation", "")
        if G.has_edge(src, tgt) and G[src][tgt].get("relation") == relation:
            G[src][tgt]["weight"] = G[src][tgt].get("weight", 1) + 1
            # Keep earliest source_location; merge call_sites if available
            if "source_location" in attrs and "source_location" in G[src][tgt]:
                existing = G[src][tgt].get("call_sites", [G[src][tgt]["source_location"]])
                if attrs["source_location"] not in existing:
                    existing.append(attrs["source_location"])
                G[src][tgt]["call_sites"] = existing
        else:
            attrs["weight"] = 1
            G.add_edge(src, tgt, **attrs)

    if "hyperedges" in extraction:
        G.graph["hyperedges"] = extraction["hyperedges"]

    return G


def build(extractions: list[dict], *, directed: bool = True) -> nx.Graph:
    """Merge multiple extraction dicts into one graph."""
    combined: dict = {"nodes": [], "edges": [], "hyperedges": []}
    for ext in extractions:
        combined["nodes"].extend(ext.get("nodes", []))
        combined["edges"].extend(ext.get("edges", []))
        combined["hyperedges"].extend(ext.get("hyperedges", []))
    return build_from_json(combined, directed=directed)


def to_node_link(G: nx.Graph) -> dict:
    """Convert NetworkX graph to node-link JSON (NetworkX standard format)."""
    from networkx.readwrite import json_graph
    try:
        data = json_graph.node_link_data(G, edges="links")
    except TypeError:
        data = json_graph.node_link_data(G)
    if "hyperedges" in G.graph:
        data["hyperedges"] = G.graph["hyperedges"]
    return data


def from_node_link(data: dict) -> nx.Graph:
    from networkx.readwrite import json_graph
    try:
        G = json_graph.node_link_graph(data, edges="links", directed=True)
    except TypeError:
        G = json_graph.node_link_graph(data, directed=True)
    if "hyperedges" in data:
        G.graph["hyperedges"] = data["hyperedges"]
    return G


def save_graph(G: nx.Graph, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_node_link(G), indent=2, ensure_ascii=False),
                    encoding="utf-8")


def load_graph(path: Path) -> nx.Graph:
    return from_node_link(json.loads(Path(path).read_text(encoding="utf-8")))


def derive_hyperedges(G: nx.Graph,
                       communities: dict[int, list[str]] | None = None,
                       max_class_methods: int = 30) -> list[dict]:
    """Derive hyperedges from existing graph structure.

    Two kinds emitted:

    1. **class_group** -- every class with `contains` edges to ≥ 2 methods.
       Members are class + all methods + decorator nodes. Useful for
       "show me everything about class X" queries.

    2. **community** -- every Leiden/Louvain community of size ≥ 3 (excluding
       file-hub-only communities). Surfaces the structure that cluster.cluster
       already discovered, but in a queryable form attached to the graph.

    Each hyperedge is a dict:
        {"id": str, "type": "class_group"|"community", "members": [node_id...],
         "label": human-readable, "size": int}
    """
    out: list[dict] = []

    # 1. class_group hyperedges
    for nid, attrs in G.nodes(data=True):
        label = attrs.get("label", "")
        # Skip file hubs and method-style nodes
        if not label or label.endswith("()") or label.startswith("@"):
            continue
        if attrs.get("file_type") not in ("code",):
            continue
        # Find members reachable via contains edges
        members: list[str] = []
        if G.is_directed():
            for _, child, edata in G.out_edges(nid, data=True):
                if edata.get("relation") == "contains":
                    members.append(child)
        else:
            for neigh in G.neighbors(nid):
                if G[nid][neigh].get("relation") == "contains":
                    members.append(neigh)
        if len(members) < 2:
            continue
        if len(members) > max_class_methods:
            members = members[:max_class_methods]
        out.append({
            "id": f"hg_class_{nid}",
            "type": "class_group",
            "label": f"class {label}",
            "members": [nid] + members,
            "size": len(members) + 1,
            "source_file": attrs.get("source_file", ""),
        })

    # 2. community hyperedges
    if communities:
        for cid, member_ids in communities.items():
            if len(member_ids) < 3:
                continue
            # Skip communities dominated by file-hub or concept nodes
            real_count = sum(
                1 for m in member_ids
                if G.nodes.get(m, {}).get("file_type") not in ("concept",)
                and not G.nodes.get(m, {}).get("label", "").endswith(".py")
            )
            if real_count < 2:
                continue
            # Use top-3 most-connected members for the label
            sorted_by_deg = sorted(
                member_ids,
                key=lambda m: G.degree(m) if m in G.nodes else 0,
                reverse=True,
            )
            top_labels = [
                G.nodes.get(m, {}).get("label", m)[:30] for m in sorted_by_deg[:3]
            ]
            out.append({
                "id": f"hg_community_{cid}",
                "type": "community",
                "label": f"community {cid}: " + ", ".join(top_labels),
                "members": list(member_ids),
                "size": len(member_ids),
            })

    return out


def attach_hyperedges(G: nx.Graph,
                       communities: dict[int, list[str]] | None = None) -> nx.Graph:
    """Compute and attach hyperedges to G.graph['hyperedges']. Returns G."""
    G.graph["hyperedges"] = derive_hyperedges(G, communities)
    return G


__all__ = ["build", "build_from_json", "to_node_link", "from_node_link",
           "save_graph", "load_graph", "derive_hyperedges", "attach_hyperedges"]
