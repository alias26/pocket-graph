"""Graph analysis: god nodes, surprising connections, suggested questions."""
from __future__ import annotations
from pathlib import Path
import networkx as nx


# Language families
_LANG_FAMILY: dict[str, str] = {
    **{e: "python" for e in (".py",)},
    **{e: "js" for e in (".js", ".jsx", ".mjs", ".ejs", ".ts", ".tsx",
                          ".vue", ".svelte")},
    **{e: "go" for e in (".go",)},
    **{e: "rust" for e in (".rs",)},
    **{e: "jvm" for e in (".java", ".kt", ".kts", ".scala")},
    **{e: "c" for e in (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp")},
    **{e: "ruby" for e in (".rb",)},
    **{e: "swift" for e in (".swift",)},
    **{e: "dotnet" for e in (".cs",)},
    **{e: "php" for e in (".php",)},
    **{e: "r" for e in (".r",)},
}


def _is_file_node(G: nx.Graph, node_id: str) -> bool:
    """File-level hub nodes (label == filename) are excluded from analysis."""
    attrs = G.nodes[node_id]
    label = attrs.get("label", "")
    if not label:
        return False
    source_file = attrs.get("source_file", "")
    if source_file and label == Path(source_file).name:
        return True
    if label.endswith("()") and G.degree(node_id) <= 1:
        return True
    return False


def _is_concept_node(G: nx.Graph, node_id: str) -> bool:
    data = G.nodes[node_id]
    return data.get("file_type") == "concept" or not data.get("source_file")


def god_nodes(G: nx.Graph, top_n: int = 10) -> list[dict]:
    """Top-N most-connected entities.

    In code graphs, file-level hubs dominate degree but are not
    semantically interesting (a file with 50 functions is not a 'god
    concept'). We exclude pure file nodes for that reason.

    Concept nodes ARE included — in paper/document graphs they typically
    are the god nodes (e.g. 'Transformer' connects to many sub-concepts);
    excluding them would hide the real structure of paper graphs.
    """
    degree = dict(G.degree())
    sorted_nodes = sorted(degree.items(), key=lambda x: x[1], reverse=True)
    result = []
    for nid, deg in sorted_nodes:
        if _is_file_node(G, nid):
            continue
        result.append({
            "id": nid,
            "label": G.nodes[nid].get("label", nid),
            "degree": deg,
            "source_file": G.nodes[nid].get("source_file", ""),
        })
        if len(result) >= top_n:
            break
    return result


def _file_category(path: str) -> str:
    if not path:
        return "concept"
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    from .detect import (CODE_EXTENSIONS, PAPER_EXTENSIONS,
                         IMAGE_EXTENSIONS)
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in PAPER_EXTENSIONS:
        return "paper"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "doc"


def _top_level_dir(path: str) -> str:
    return path.split("/")[0] if "/" in path else path


def _cross_language(src_a: str, src_b: str) -> bool:
    ext_a = Path(src_a).suffix.lower()
    ext_b = Path(src_b).suffix.lower()
    fam_a = _LANG_FAMILY.get(ext_a)
    fam_b = _LANG_FAMILY.get(ext_b)
    if fam_a is None or fam_b is None:
        return False
    return fam_a != fam_b


def surprising_connections(G: nx.Graph,
                            communities: dict[int, list[str]] | None = None,
                            top_n: int = 5) -> list[dict]:
    """Find non-obvious cross-file/cross-community edges between real entities."""
    node_community = {}
    if communities:
        for cid, nodes in communities.items():
            for n in nodes:
                node_community[n] = cid

    candidates: list[tuple[int, str, str, dict, list[str]]] = []
    for u, v, data in G.edges(data=True):
        if _is_file_node(G, u) or _is_file_node(G, v):
            continue
        if _is_concept_node(G, u) or _is_concept_node(G, v):
            continue
        u_src = G.nodes[u].get("source_file", "")
        v_src = G.nodes[v].get("source_file", "")
        if u_src == v_src:
            continue  # same-file edges aren't surprising

        score = 0
        reasons: list[str] = []

        conf = data.get("confidence", "EXTRACTED")
        relation = data.get("relation", "")
        conf_bonus = {"AMBIGUOUS": 3, "INFERRED": 2, "EXTRACTED": 1}.get(conf, 1)
        if conf == "INFERRED" and relation == "calls" and _cross_language(u_src, v_src):
            conf_bonus = 0
        score += conf_bonus
        if conf in ("AMBIGUOUS", "INFERRED"):
            reasons.append(f"{conf.lower()} connection")

        cat_u = _file_category(u_src)
        cat_v = _file_category(v_src)
        if cat_u != cat_v:
            score += 2
            reasons.append(f"crosses file types ({cat_u} ↔ {cat_v})")

        if _top_level_dir(u_src) != _top_level_dir(v_src):
            score += 2
            reasons.append("connects across different directories")

        cid_u = node_community.get(u)
        cid_v = node_community.get(v)
        if cid_u is not None and cid_v is not None and cid_u != cid_v:
            score += 1
            reasons.append("crosses Leiden communities")

        candidates.append((score, u, v, data, reasons))

    candidates.sort(key=lambda x: -x[0])
    out = []
    for score, u, v, data, reasons in candidates[:top_n]:
        out.append({
            "source": u,
            "source_label": G.nodes[u].get("label", u),
            "target": v,
            "target_label": G.nodes[v].get("label", v),
            "relation": data.get("relation", ""),
            "confidence": data.get("confidence", ""),
            "score": score,
            "reasons": reasons,
        })
    return out


def suggested_questions(G: nx.Graph,
                         gods: list[dict],
                         surprises: list[dict]) -> list[str]:
    """Generate questions the graph is uniquely positioned to answer."""
    qs: list[str] = []
    if gods:
        top = gods[0]
        qs.append(f"What does {top['label']} connect to and why is it central?")
        if len(gods) > 1:
            qs.append(f"How do {gods[0]['label']} and {gods[1]['label']} relate?")
    if surprises:
        s = surprises[0]
        qs.append(f"Why does {s['source_label']} {s['relation']} {s['target_label']}?")
    qs.append("Which subsystem has the highest internal cohesion?")
    qs.append("Are there any AMBIGUOUS connections that need manual review?")
    return qs[:5]


def analyze(G: nx.Graph,
            communities: dict[int, list[str]] | None = None) -> dict:
    """Run all analyses, return a structured dict."""
    gods = god_nodes(G)
    surprises = surprising_connections(G, communities)
    questions = suggested_questions(G, gods, surprises)

    # Confidence distribution
    conf_dist: dict[str, int] = {}
    for _, _, data in G.edges(data=True):
        c = data.get("confidence", "?")
        conf_dist[c] = conf_dist.get(c, 0) + 1

    # Per-community summary
    community_summary = []
    if communities:
        for cid, nodes in sorted(communities.items())[:10]:
            community_summary.append({
                "id": cid,
                "size": len(nodes),
                "sample_labels": [G.nodes[n].get("label", n) for n in nodes[:5]],
            })

    return {
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "god_nodes": gods,
        "surprising_connections": surprises,
        "suggested_questions": questions,
        "confidence_distribution": conf_dist,
        "communities": community_summary,
    }


__all__ = ["god_nodes", "surprising_connections", "suggested_questions",
           "analyze", "cohesion_score"]
