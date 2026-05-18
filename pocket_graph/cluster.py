"""Community detection. Tries Leiden (graspologic), falls back to Louvain."""
from __future__ import annotations
import inspect
import networkx as nx


_MAX_COMMUNITY_FRACTION = 0.25
_MIN_SPLIT_SIZE = 10
_COHESION_SPLIT_THRESHOLD = 0.05
_COHESION_SPLIT_MIN_SIZE = 50


def _partition(G: nx.Graph) -> dict[str, int]:
    """Run community detection, return {node_id: community_id}.

    Uses edge `weight` attribute when present -- heavier edges (more frequent
    calls / imports) pull nodes into the same community more strongly.
    """
    try:
        from graspologic.partition import leiden
        return leiden(G)
    except ImportError:
        pass
    # Louvain fallback (built-in to networkx)
    kwargs: dict = {"seed": 42, "threshold": 1e-4, "weight": "weight"}
    if "max_level" in inspect.signature(nx.community.louvain_communities).parameters:
        kwargs["max_level"] = 10
    communities = nx.community.louvain_communities(G, **kwargs)
    return {node: cid for cid, nodes in enumerate(communities) for node in nodes}


def cohesion_score(G: nx.Graph, community_nodes: list[str]) -> float:
    n = len(community_nodes)
    if n <= 1:
        return 1.0
    sub = G.subgraph(community_nodes)
    actual = sub.number_of_edges()
    possible = n * (n - 1) / 2
    return round(actual / possible, 2) if possible > 0 else 0.0


def _split_community(G: nx.Graph, nodes: list[str]) -> list[list[str]]:
    sub = G.subgraph(nodes)
    if sub.number_of_edges() == 0:
        return [[n] for n in sorted(nodes)]
    try:
        sub_part = _partition(sub)
        sub_communities: dict[int, list[str]] = {}
        for node, cid in sub_part.items():
            sub_communities.setdefault(cid, []).append(node)
        if len(sub_communities) <= 1:
            return [sorted(nodes)]
        return [sorted(v) for v in sub_communities.values()]
    except Exception:
        return [sorted(nodes)]


def cluster(G: nx.Graph) -> dict[int, list[str]]:
    """Run community detection. Returns {community_id: [node_ids]}.
    
    Community 0 is largest. Oversized communities (>25% of nodes) split via
    a second pass. Low-cohesion communities (<5%) also split.

    Annotates `community` attribute on the input graph's nodes (in-place).
    """
    if G.number_of_nodes() == 0:
        return {}
    original_G = G  # keep reference for annotation
    if G.is_directed():
        G = G.to_undirected()
    if G.number_of_edges() == 0:
        return {i: [n] for i, n in enumerate(sorted(G.nodes))}

    isolates = [n for n in G.nodes() if G.degree(n) == 0]
    connected_nodes = [n for n in G.nodes() if G.degree(n) > 0]
    connected = G.subgraph(connected_nodes)

    raw: dict[int, list[str]] = {}
    if connected.number_of_nodes() > 0:
        partition = _partition(connected)
        for node, cid in partition.items():
            raw.setdefault(cid, []).append(node)

    next_cid = max(raw.keys(), default=-1) + 1
    for node in isolates:
        raw[next_cid] = [node]
        next_cid += 1

    max_size = max(_MIN_SPLIT_SIZE, int(G.number_of_nodes() * _MAX_COMMUNITY_FRACTION))
    final: list[list[str]] = []
    for nodes in raw.values():
        if len(nodes) > max_size:
            final.extend(_split_community(G, nodes))
        else:
            final.append(nodes)

    # Second pass: cohesion-based split
    second: list[list[str]] = []
    for nodes in final:
        if (len(nodes) >= _COHESION_SPLIT_MIN_SIZE
                and cohesion_score(G, nodes) < _COHESION_SPLIT_THRESHOLD):
            splits = _split_community(G, nodes)
            second.extend(splits if len(splits) > 1 else [nodes])
        else:
            second.append(nodes)

    second.sort(key=len, reverse=True)
    result = {i: sorted(nodes) for i, nodes in enumerate(second)}

    # Annotate the graph (both undirected working copy and original)
    for cid, nodes in result.items():
        for n in nodes:
            if n in G.nodes:
                G.nodes[n]["community"] = cid
            if n in original_G.nodes:
                original_G.nodes[n]["community"] = cid
    return result


def score_all(G: nx.Graph, communities: dict[int, list[str]]) -> dict[int, float]:
    return {cid: cohesion_score(G, nodes) for cid, nodes in communities.items()}


__all__ = ["cluster", "cohesion_score", "score_all"]
