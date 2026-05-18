"""Graph traversal queries: path, neighbors, subgraph, label search, BFS/DFS query.

Scoring also matches against pocket-graph's `tags` and `description` node fields.
"""
from __future__ import annotations
import re
import unicodedata
import networkx as nx


# ============================================================
# Helpers
# ============================================================

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_LABEL_LEN = 256


def sanitize_label(text: str | None) -> str:
    """Strip control characters and cap length."""
    if text is None:
        return ""
    text = _CONTROL_CHAR_RE.sub("", str(text))
    if len(text) > _MAX_LABEL_LEN:
        text = text[:_MAX_LABEL_LEN]
    return text


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


_EXACT_MATCH_BONUS = 100.0


def _score_nodes(G: nx.Graph, terms: list[str]) -> list[tuple[float, str]]:
    """Score every node by how well it matches the query terms.

    Scoring weighs `label`, `source_file`, `tags`, and `description`.
    """
    scored = []
    norm_terms = [_strip_diacritics(t).lower() for t in terms]
    for nid, data in G.nodes(data=True):
        norm_label = data.get("norm_label") or _strip_diacritics(data.get("label") or "").lower()
        source = (data.get("source_file") or "").lower()

        # base score: label and source_file matches
        score = sum(1 for t in norm_terms if t in norm_label) + \
                sum(0.5 for t in norm_terms if t in source)

        # exact match bonus
        if any(t == norm_label or t == norm_label.rstrip("()") for t in norm_terms):
            score += _EXACT_MATCH_BONUS

        # tag and description matches
        for tag in data.get("tags") or []:
            tag_lower = _strip_diacritics(str(tag)).lower()
            score += sum(2 for t in norm_terms if t in tag_lower)

        desc = _strip_diacritics(data.get("description") or "").lower()
        if desc:
            score += sum(0.3 for t in norm_terms if t in desc)

        if score > 0:
            scored.append((score, nid))
    return sorted(scored, reverse=True)


_CONTEXT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("call", ("call", "calls", "called", "invoke", "invokes", "invoked")),
    ("import", ("import", "imports", "imported", "module", "modules")),
    ("field", ("field", "fields", "member", "members", "property", "properties")),
    ("parameter_type", ("parameter", "parameters", "param", "params", "argument", "arguments")),
    ("return_type", ("return", "returns", "returned")),
    ("generic_arg", ("generic", "generics", "template", "templates")),
)


def _normalize_context_filters(filters: list[str] | None) -> list[str]:
    if not filters:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in filters:
        key = _strip_diacritics(str(value)).strip().lower()
        if key and key not in seen:
            seen.add(key)
            normalized.append(key)
    return normalized


def _infer_context_filters(question: str) -> list[str]:
    lowered = {
        _strip_diacritics(token).lower()
        for token in question.replace("?", " ").replace(",", " ").split()
    }
    inferred: list[str] = []
    for context, hints in _CONTEXT_HINTS:
        if any(hint in lowered for hint in hints):
            inferred.append(context)
    return inferred


def _resolve_context_filters(question: str, explicit_filters: list[str] | None = None) -> tuple[list[str], str | None]:
    normalized = _normalize_context_filters(explicit_filters)
    if normalized:
        return normalized, "explicit"
    inferred = _infer_context_filters(question)
    if inferred:
        return inferred, "heuristic"
    return [], None


def _filter_graph_by_context(G: nx.Graph, context_filters: list[str] | None) -> nx.Graph:
    filters = set(_normalize_context_filters(context_filters))
    if not filters:
        return G
    H = G.__class__()
    H.add_nodes_from(G.nodes(data=True))
    if isinstance(G, (nx.MultiGraph, nx.MultiDiGraph)):
        for u, v, key, data in G.edges(keys=True, data=True):
            if data.get("context") in filters:
                H.add_edge(u, v, key=key, **data)
    else:
        for u, v, data in G.edges(data=True):
            if data.get("context") in filters:
                H.add_edge(u, v, **data)
    return H


def _bfs(G: nx.Graph, start_nodes: list[str], depth: int) -> tuple[set[str], list[tuple]]:
    visited: set[str] = set(start_nodes)
    frontier = set(start_nodes)
    edges_seen: list[tuple] = []
    for _ in range(depth):
        next_frontier: set[str] = set()
        for n in frontier:
            for neighbor in G.neighbors(n):
                if neighbor not in visited:
                    next_frontier.add(neighbor)
                    edges_seen.append((n, neighbor))
        visited.update(next_frontier)
        frontier = next_frontier
    return visited, edges_seen


def _dfs(G: nx.Graph, start_nodes: list[str], depth: int) -> tuple[set[str], list[tuple]]:
    visited: set[str] = set()
    edges_seen: list[tuple] = []
    stack = [(n, 0) for n in reversed(start_nodes)]
    while stack:
        node, d = stack.pop()
        if node in visited or d > depth:
            continue
        visited.add(node)
        for neighbor in G.neighbors(node):
            if neighbor not in visited:
                stack.append((neighbor, d + 1))
                edges_seen.append((node, neighbor))
    return visited, edges_seen


def _subgraph_to_text(G: nx.Graph, nodes: set[str], edges: list[tuple],
                       token_budget: int = 2000, *, seeds: list[str] | None = None) -> str:
    """Render subgraph as text, truncating at ~token_budget (3 chars/token approx).

    seeds: exact-match nodes rendered first before the degree-sorted expansion,
    so the queried symbol always appears at the top of the output.
    """
    char_budget = token_budget * 3
    lines: list[str] = []
    seed_set = set(seeds or [])
    ordered = [n for n in (seeds or []) if n in nodes] + \
              sorted(nodes - seed_set, key=lambda n: G.degree(n), reverse=True)
    for nid in ordered:
        d = G.nodes[nid]
        # pocket-graph extension: include tags + description if present
        extras = []
        if d.get("tags"):
            extras.append(f"tags={','.join(d['tags'][:5])}")
        if d.get("description"):
            desc = sanitize_label(d['description'])[:120]
            extras.append(f"desc={desc}")
        extra_str = (" " + " ".join(extras)) if extras else ""
        line = (f"NODE {sanitize_label(d.get('label', nid))} "
                f"[id={nid} src={d.get('source_file', '')} "
                f"type={d.get('file_type', '')}{extra_str}]")
        lines.append(line)
    for u, v in edges:
        if u in nodes and v in nodes:
            raw = G[u][v]
            d = next(iter(raw.values()), {}) if isinstance(G, (nx.MultiGraph, nx.MultiDiGraph)) else raw
            context = d.get("context")
            context_suffix = f" context={context}" if context else ""
            line = (
                f"EDGE {sanitize_label(G.nodes[u].get('label', u))} "
                f"--{d.get('relation', '')} [{d.get('confidence', '')}{context_suffix}]--> "
                f"{sanitize_label(G.nodes[v].get('label', v))}"
            )
            lines.append(line)
    output = "\n".join(lines)
    if len(output) > char_budget:
        output = output[:char_budget] + f"\n... (truncated to ~{token_budget} token budget)"
    return output


def query_graph_text(
    G: nx.Graph,
    question: str,
    *,
    mode: str = "bfs",
    depth: int = 3,
    token_budget: int = 2000,
    context_filters: list[str] | None = None,
) -> str:
    """BFS/DFS traversal answering a question. Returns subgraph as text.

    Algorithm:
        1. Tokenize question into terms (>2 chars).
        2. Score every node by term matches in label/source/tags/description.
        3. Take top 3 as seed start nodes.
        4. BFS (default) or DFS to `depth` from seeds.
        5. Optionally filter edges by context (call/import/field/...).
        6. Render subgraph as text within token_budget.
    """
    terms = [t.lower() for t in question.split() if len(t) > 2]
    scored = _score_nodes(G, terms)
    start_nodes = [nid for _, nid in scored[:3]]
    if not start_nodes:
        return "No matching nodes found."
    resolved_filters, filter_source = _resolve_context_filters(question, context_filters)
    traversal_graph = _filter_graph_by_context(G, resolved_filters)
    nodes, edges = _dfs(traversal_graph, start_nodes, depth) if mode == "dfs" \
                   else _bfs(traversal_graph, start_nodes, depth)
    header_parts = [
        f"Traversal: {mode.upper()} depth={depth}",
        f"Start: {[G.nodes[n].get('label', n) for n in start_nodes]}",
    ]
    if resolved_filters:
        header_parts.append(f"Context: {', '.join(resolved_filters)} ({filter_source})")
    header_parts.append(f"{len(nodes)} nodes found")
    header = " | ".join(header_parts) + "\n\n"
    return header + _subgraph_to_text(traversal_graph, nodes, edges, token_budget,
                                        seeds=start_nodes)


# ============================================================
# Original pocket-graph functions
# ============================================================


def find_nodes(G: nx.Graph, query: str, limit: int = 10) -> list[dict]:
    """Find nodes by multi-term scoring.

    Scoring (per node):
      +1.0 per term in label
      +0.5 per term in source_file
      +2.0 per term in tags
      +0.3 per term in description
      +100 if any term exactly equals the label (with optional trailing ())

    Falls back to legacy substring match if no terms have >2 chars (so a
    one-letter or all-stopword query still returns something).
    """
    terms = [t.lower() for t in query.split() if len(t) > 2]

    if not terms:
        # Legacy substring fallback for very short queries
        q = query.lower()
        matches = []
        for nid, data in G.nodes(data=True):
            label = data.get("label", "")
            if q in label.lower() or q in nid.lower():
                matches.append({
                    "id": nid, "label": label,
                    "file_type": data.get("file_type", ""),
                    "source_file": data.get("source_file", ""),
                    "degree": G.degree(nid),
                    "score": 1.0,
                })
        matches.sort(key=lambda x: -x["degree"])
        return matches[:limit]

    scored = _score_nodes(G, terms)
    matches = []
    for score, nid in scored[:limit]:
        data = G.nodes[nid]
        matches.append({
            "id": nid,
            "label": data.get("label", ""),
            "file_type": data.get("file_type", ""),
            "source_file": data.get("source_file", ""),
            "degree": G.degree(nid),
            "score": round(score, 2),
        })
    return matches


def get_neighbors(G: nx.Graph, node_id: str,
                   depth: int = 1, max_nodes: int = 30) -> dict:
    """Return a subgraph centered on node_id, up to `depth` hops away."""
    if node_id not in G.nodes:
        return {"error": f"node {node_id!r} not found"}
    if G.is_directed():
        UG = G.to_undirected(as_view=True)
    else:
        UG = G
    visited = {node_id}
    frontier = {node_id}
    for _ in range(depth):
        next_frontier = set()
        for n in frontier:
            for neigh in UG.neighbors(n):
                if neigh not in visited:
                    next_frontier.add(neigh)
                    visited.add(neigh)
                    if len(visited) >= max_nodes:
                        break
            if len(visited) >= max_nodes:
                break
        frontier = next_frontier
        if not frontier:
            break

    nodes = []
    for n in visited:
        node_data = {
            "id": n, "label": G.nodes[n].get("label", n),
            "source_file": G.nodes[n].get("source_file", ""),
            "file_type": G.nodes[n].get("file_type", ""),
            "distance": nx.shortest_path_length(UG, node_id, n) if n != node_id else 0,
        }
        # Include body for rationale nodes so docstring full text is accessible
        if G.nodes[n].get("file_type") == "rationale" and "body" in G.nodes[n]:
            node_data["body"] = G.nodes[n]["body"]
        nodes.append(node_data)
    edges = []
    for u, v, data in G.edges(data=True):
        if u in visited and v in visited:
            edges.append({
                "source": u, "target": v,
                "relation": data.get("relation", ""),
                "confidence": data.get("confidence", ""),
            })
    return {"center": node_id, "nodes": nodes, "edges": edges}


def shortest_path(G: nx.Graph, source: str, target: str) -> dict:
    """Return the shortest path between two nodes (treating graph as undirected)."""
    if source not in G.nodes:
        return {"error": f"source {source!r} not found"}
    if target not in G.nodes:
        return {"error": f"target {target!r} not found"}

    UG = G.to_undirected(as_view=True) if G.is_directed() else G
    try:
        path = nx.shortest_path(UG, source, target)
    except nx.NetworkXNoPath:
        return {"error": "no path between nodes"}

    nodes = [{"id": n, "label": G.nodes[n].get("label", n)} for n in path]
    edges = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        # Find the edge attrs in either direction
        if G.has_edge(u, v):
            data = G[u][v]
        elif G.has_edge(v, u):
            data = G[v][u]
        else:
            data = {}
        edges.append({
            "source": u, "target": v,
            "relation": data.get("relation", ""),
            "confidence": data.get("confidence", ""),
        })
    return {"source": source, "target": target,
            "length": len(path) - 1,
            "nodes": nodes, "edges": edges}


def filter_by_relation(G: nx.Graph, relation: str) -> list[dict]:
    """Return all edges with a specific relation type."""
    out = []
    for u, v, data in G.edges(data=True):
        if data.get("relation") == relation:
            out.append({
                "source": u, "source_label": G.nodes[u].get("label", u),
                "target": v, "target_label": G.nodes[v].get("label", v),
                "confidence": data.get("confidence", ""),
                "source_file": data.get("source_file", ""),
            })
    return out


def filter_by_decorator(G: nx.Graph, decorator: str) -> list[dict]:
    """Find all functions/classes that use a specific decorator.
    
    Decorators are stored as nodes labeled '@<text>' connected by `uses` edge.
    """
    decorator_normalized = decorator.lstrip("@").lower()
    decorator_nodes = []
    for nid, data in G.nodes(data=True):
        label = data.get("label", "")
        if label.startswith("@") and decorator_normalized in label.lower():
            decorator_nodes.append(nid)

    users = []
    for dec_nid in decorator_nodes:
        # Edges where someone --uses--> this decorator
        UG = G.to_undirected(as_view=True) if G.is_directed() else G
        for neighbor in UG.neighbors(dec_nid):
            data = G[neighbor][dec_nid] if G.has_edge(neighbor, dec_nid) else G[dec_nid][neighbor]
            if data.get("relation") == "uses":
                users.append({
                    "id": neighbor,
                    "label": G.nodes[neighbor].get("label", neighbor),
                    "decorator": G.nodes[dec_nid].get("label", dec_nid),
                    "source_file": G.nodes[neighbor].get("source_file", ""),
                })
    return users


def callees_of(G: nx.Graph, function_id: str) -> list[dict]:
    """All functions called by the given function. Sorted by call weight desc."""
    if function_id not in G.nodes:
        return []
    out = []
    for _, target, data in G.out_edges(function_id, data=True) if G.is_directed() else []:
        if data.get("relation") == "calls":
            out.append({
                "id": target,
                "label": G.nodes[target].get("label", target),
                "source_file": G.nodes[target].get("source_file", ""),
                "confidence": data.get("confidence", ""),
                "weight": data.get("weight", 1),
            })
    out.sort(key=lambda e: e["weight"], reverse=True)
    return out


def callers_of(G: nx.Graph, function_id: str) -> list[dict]:
    """All functions that call the given function. Sorted by call weight desc."""
    if function_id not in G.nodes:
        return []
    out = []
    if not G.is_directed():
        return out
    for source, _, data in G.in_edges(function_id, data=True):
        if data.get("relation") == "calls":
            out.append({
                "id": source,
                "label": G.nodes[source].get("label", source),
                "source_file": G.nodes[source].get("source_file", ""),
                "confidence": data.get("confidence", ""),
                "weight": data.get("weight", 1),
            })
    out.sort(key=lambda e: e["weight"], reverse=True)
    return out


def find_hyperedges_for(G: nx.Graph, node_id: str) -> list[dict]:
    """Return all hyperedges that include the given node_id as a member."""
    out = []
    for he in G.graph.get("hyperedges", []):
        if node_id in he.get("members", []):
            out.append(he)
    return out


def list_hyperedges(G: nx.Graph,
                     hyperedge_type: str | None = None) -> list[dict]:
    """List all hyperedges, optionally filtered by type ('class_group'|'community')."""
    hyperedges = G.graph.get("hyperedges", [])
    if hyperedge_type is None:
        return list(hyperedges)
    return [he for he in hyperedges if he.get("type") == hyperedge_type]


def bridging_nodes(G: nx.Graph,
                    hyperedge_a: str,
                    hyperedge_b: str,
                    limit: int = 10) -> list[dict]:
    """Find nodes that connect two hyperedges (typically two communities).

    A bridging node is one that has edges to members of both hyperedges
    *or* is itself a member of one and has edges to the other. Useful for
    architectural insight: which classes/files glue two subsystems together.

    Returns a list sorted by `bridge_strength` (number of cross-hyperedge edges).
    """
    hyperedges = G.graph.get("hyperedges", [])
    he_a = next((h for h in hyperedges if h.get("id") == hyperedge_a), None)
    he_b = next((h for h in hyperedges if h.get("id") == hyperedge_b), None)
    if he_a is None or he_b is None:
        return [{"error": f"hyperedge not found: {hyperedge_a if he_a is None else hyperedge_b}"}]

    members_a = set(he_a.get("members", []))
    members_b = set(he_b.get("members", []))
    overlap = members_a & members_b  # nodes in both -- already bridges

    # For each node in graph, count edges to A and to B
    bridges: list[tuple[str, int, int]] = []
    for n in G.nodes:
        if n in members_a and n in members_b:
            # Member of both -- bridge by definition
            bridges.append((n, len(members_b), len(members_a)))
            continue
        edges_to_a = 0
        edges_to_b = 0
        if G.is_directed():
            for nbr in list(G.successors(n)) + list(G.predecessors(n)):
                if nbr in members_a and n != nbr:
                    edges_to_a += 1
                if nbr in members_b and n != nbr:
                    edges_to_b += 1
        else:
            for nbr in G.neighbors(n):
                if nbr in members_a and n != nbr:
                    edges_to_a += 1
                if nbr in members_b and n != nbr:
                    edges_to_b += 1
        if edges_to_a >= 1 and edges_to_b >= 1:
            bridges.append((n, edges_to_a, edges_to_b))

    bridges.sort(key=lambda x: x[1] + x[2], reverse=True)
    out = []
    for nid, ea, eb in bridges[:limit]:
        out.append({
            "id": nid,
            "label": G.nodes[nid].get("label", nid),
            "source_file": G.nodes[nid].get("source_file", ""),
            "edges_to_a": ea,
            "edges_to_b": eb,
            "bridge_strength": ea + eb,
            "in_both": nid in overlap,
        })
    return out


def centrality_within(G: nx.Graph, hyperedge_id: str) -> list[dict]:
    """Compute degree centrality for every member of a hyperedge.

    The centrality is computed on the subgraph induced by the hyperedge's
    members -- this surfaces the "hub" within a class or community without
    being polluted by unrelated edges from elsewhere in the graph.

    Returns members sorted by their internal degree (most central first).
    """
    hyperedges = G.graph.get("hyperedges", [])
    he = next((h for h in hyperedges if h.get("id") == hyperedge_id), None)
    if he is None:
        return [{"error": f"hyperedge not found: {hyperedge_id}"}]

    members = he.get("members", [])
    if not members:
        return []

    sub = G.subgraph(members)
    out = []
    for nid in members:
        if nid not in sub:
            continue
        deg = sub.degree(nid)
        # Weighted degree (sum of edge weights)
        wdeg = sum(d.get("weight", 1) for _, _, d in sub.edges(nid, data=True))
        out.append({
            "id": nid,
            "label": G.nodes[nid].get("label", nid),
            "source_file": G.nodes[nid].get("source_file", ""),
            "degree": deg,
            "weighted_degree": wdeg,
            "file_type": G.nodes[nid].get("file_type", ""),
        })
    out.sort(key=lambda x: (x["weighted_degree"], x["degree"]), reverse=True)
    return out


def evolution_diff(old_graph_path: str | "Path",
                    new_graph_path: str | "Path") -> dict:
    """Compute the structural diff between two graph snapshots.

    Returns:
      {
        "added_nodes": [...],        # in new but not old
        "removed_nodes": [...],      # in old but not new
        "added_edges": [...],        # (src, tgt, relation) in new but not old
        "removed_edges": [...],
        "moved_communities": [...],  # node_id whose community changed
        "summary": "...",
      }

    Useful for: refactoring impact analysis, "what changed since last week",
    detecting accidental drift in community structure.
    """
    from pathlib import Path
    from .build import load_graph
    G_old = load_graph(Path(old_graph_path))
    G_new = load_graph(Path(new_graph_path))

    nodes_old = set(G_old.nodes)
    nodes_new = set(G_new.nodes)
    added_nodes = nodes_new - nodes_old
    removed_nodes = nodes_old - nodes_new

    def edge_set(G):
        return {(u, v, d.get("relation", ""))
                for u, v, d in G.edges(data=True)}

    edges_old = edge_set(G_old)
    edges_new = edge_set(G_new)
    added_edges = edges_new - edges_old
    removed_edges = edges_old - edges_new

    # Community drift: which surviving nodes changed community membership.
    # Communities are matched by membership overlap (Jaccard) since IDs are arbitrary.
    moved = []
    common = nodes_old & nodes_new
    if common:
        old_comm = {n: G_old.nodes[n].get("community") for n in common}
        new_comm = {n: G_new.nodes[n].get("community") for n in common}

        # Build community -> members maps
        old_groups: dict = {}
        new_groups: dict = {}
        for n, c in old_comm.items():
            if c is not None:
                old_groups.setdefault(c, set()).add(n)
        for n, c in new_comm.items():
            if c is not None:
                new_groups.setdefault(c, set()).add(n)

        # Match each old community to the new one that shares the most members
        old_to_new: dict = {}
        for old_cid, old_members in old_groups.items():
            best, best_jacc = None, 0.0
            for new_cid, new_members in new_groups.items():
                inter = len(old_members & new_members)
                union = len(old_members | new_members)
                if union > 0 and inter / union > best_jacc:
                    best, best_jacc = new_cid, inter / union
            old_to_new[old_cid] = best

        for n in common:
            mapped_new_for_old = old_to_new.get(old_comm[n])
            if mapped_new_for_old is not None and mapped_new_for_old != new_comm[n]:
                moved.append({
                    "id": n,
                    "label": G_new.nodes[n].get("label", n),
                    "old_community": old_comm[n],
                    "new_community": new_comm[n],
                })

    summary = (f"+{len(added_nodes)} nodes, -{len(removed_nodes)} nodes, "
                f"+{len(added_edges)} edges, -{len(removed_edges)} edges, "
                f"{len(moved)} community moves")

    return {
        "added_nodes": [
            {"id": n, "label": G_new.nodes[n].get("label", n),
             "file_type": G_new.nodes[n].get("file_type", "")}
            for n in sorted(added_nodes)
        ][:50],
        "removed_nodes": [
            {"id": n, "label": G_old.nodes[n].get("label", n),
             "file_type": G_old.nodes[n].get("file_type", "")}
            for n in sorted(removed_nodes)
        ][:50],
        "added_edges": [
            {"source": u, "target": v, "relation": r}
            for u, v, r in sorted(added_edges)
        ][:50],
        "removed_edges": [
            {"source": u, "target": v, "relation": r}
            for u, v, r in sorted(removed_edges)
        ][:50],
        "moved_communities": moved[:50],
        "summary": summary,
        "totals": {
            "added_nodes": len(added_nodes),
            "removed_nodes": len(removed_nodes),
            "added_edges": len(added_edges),
            "removed_edges": len(removed_edges),
            "moved_communities": len(moved),
        },
    }


__all__ = ["find_nodes", "get_neighbors", "shortest_path",
           "filter_by_relation", "filter_by_decorator",
           "callees_of", "callers_of",
           "find_hyperedges_for", "list_hyperedges",
           "bridging_nodes", "centrality_within", "evolution_diff"]
