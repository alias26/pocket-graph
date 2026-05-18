"""MCP-style stdio JSON-RPC server for pocket-graph.

Implements the MCP tool protocol over stdio so a graph.json can be queried
by Claude Code, Codex, etc. Tools exposed:

  - find_nodes(query, limit?) -> list of nodes matching label/id
  - get_node(node_id) -> full node attrs + neighbors
  - get_neighbors(node_id, depth?) -> subgraph
  - shortest_path(source, target) -> path nodes + edges
  - filter_by_relation(relation) -> all edges of that type
  - filter_by_decorator(decorator) -> users of a decorator
  - callees_of(node_id) / callers_of(node_id)

Wire format: line-delimited JSON-RPC 2.0 (the same wire format MCP uses
for stdio transport).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

from .build import load_graph
from . import query as pgq


_TOOLS = [
    {
        "name": "find_nodes",
        "description": "Find graph nodes whose label or id contains the query string.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_node",
        "description": "Return the full attributes of one node, plus its immediate neighbors.",
        "inputSchema": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
    },
    {
        "name": "get_neighbors",
        "description": "Return a subgraph centered on a node, up to `depth` hops.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "depth": {"type": "integer", "default": 1},
                "max_nodes": {"type": "integer", "default": 30},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "shortest_path",
        "description": "Find the shortest path between two nodes (graph treated as undirected).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
            },
            "required": ["source", "target"],
        },
    },
    {
        "name": "filter_by_relation",
        "description": "Return all edges with a specific relation type (calls, imports, contains, etc).",
        "inputSchema": {
            "type": "object",
            "properties": {"relation": {"type": "string"}},
            "required": ["relation"],
        },
    },
    {
        "name": "filter_by_decorator",
        "description": "Find functions/classes that use a specific decorator.",
        "inputSchema": {
            "type": "object",
            "properties": {"decorator": {"type": "string"}},
            "required": ["decorator"],
        },
    },
    {
        "name": "callees_of",
        "description": "All functions called by the given function.",
        "inputSchema": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
    },
    {
        "name": "callers_of",
        "description": "All functions that call the given function.",
        "inputSchema": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
    },
    {
        "name": "find_hyperedges_for",
        "description": "Return all hyperedges (class groups or communities) that include this node.",
        "inputSchema": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
    },
    {
        "name": "list_hyperedges",
        "description": "List all hyperedges, optionally filtered by type ('class_group' or 'community').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hyperedge_type": {"type": "string", "enum": ["class_group", "community"]},
            },
        },
    },
    {
        "name": "bridging_nodes",
        "description": "Find nodes that bridge two hyperedges -- useful for identifying the 'glue' classes/files between two subsystems.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hyperedge_a": {"type": "string"},
                "hyperedge_b": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["hyperedge_a", "hyperedge_b"],
        },
    },
    {
        "name": "centrality_within",
        "description": "Compute degree centrality for every member of a hyperedge -- surfaces the hub of a class or community.",
        "inputSchema": {
            "type": "object",
            "properties": {"hyperedge_id": {"type": "string"}},
            "required": ["hyperedge_id"],
        },
    },
    {
        "name": "evolution_diff",
        "description": "Compute structural diff between two graph snapshots (added/removed nodes, added/removed edges, community moves).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "old_graph_path": {"type": "string"},
                "new_graph_path": {"type": "string"},
            },
            "required": ["old_graph_path", "new_graph_path"],
        },
    },
]


def _dispatch(G, tool_name: str, args: dict):
    """Run one tool call against the graph."""
    if tool_name == "find_nodes":
        return pgq.find_nodes(G, args["query"], args.get("limit", 10))
    if tool_name == "get_node":
        nid = args["node_id"]
        if nid not in G.nodes:
            return {"error": f"node {nid!r} not found"}
        attrs = dict(G.nodes[nid])
        return {
            "id": nid,
            "attrs": attrs,
            "neighbors": pgq.get_neighbors(G, nid, depth=1)["nodes"],
        }
    if tool_name == "get_neighbors":
        return pgq.get_neighbors(
            G, args["node_id"],
            depth=args.get("depth", 1),
            max_nodes=args.get("max_nodes", 30),
        )
    if tool_name == "shortest_path":
        return pgq.shortest_path(G, args["source"], args["target"])
    if tool_name == "filter_by_relation":
        return pgq.filter_by_relation(G, args["relation"])
    if tool_name == "filter_by_decorator":
        return pgq.filter_by_decorator(G, args["decorator"])
    if tool_name == "callees_of":
        return pgq.callees_of(G, args["node_id"])
    if tool_name == "callers_of":
        return pgq.callers_of(G, args["node_id"])
    if tool_name == "find_hyperedges_for":
        return pgq.find_hyperedges_for(G, args["node_id"])
    if tool_name == "list_hyperedges":
        return pgq.list_hyperedges(G, args.get("hyperedge_type"))
    if tool_name == "bridging_nodes":
        return pgq.bridging_nodes(G, args["hyperedge_a"], args["hyperedge_b"],
                                    limit=args.get("limit", 10))
    if tool_name == "centrality_within":
        return pgq.centrality_within(G, args["hyperedge_id"])
    if tool_name == "evolution_diff":
        return pgq.evolution_diff(args["old_graph_path"], args["new_graph_path"])
    return {"error": f"unknown tool {tool_name!r}"}


def _make_response(req_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def serve(graph_path: Path, in_stream=None, out_stream=None) -> None:
    """Run the MCP stdio server against the given graph.

    Reads JSON-RPC 2.0 messages line-delimited from stdin, writes responses
    to stdout. Compatible with the MCP stdio transport used by Claude Code,
    Codex, and other MCP clients.
    """
    G = load_graph(Path(graph_path))
    in_s = in_stream or sys.stdin
    out_s = out_stream or sys.stdout

    def write(obj):
        out_s.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out_s.flush()

    for line in in_s:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            write(_make_response(None, error={"code": -32700, "message": str(e)}))
            continue

        method = req.get("method")
        rid = req.get("id")
        params = req.get("params", {})

        try:
            if method == "initialize":
                write(_make_response(rid, result={
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "pocket-graph", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                }))
            elif method == "tools/list":
                write(_make_response(rid, result={"tools": _TOOLS}))
            elif method == "tools/call":
                tool_name = params.get("name")
                args = params.get("arguments", {}) or {}
                result = _dispatch(G, tool_name, args)
                write(_make_response(rid, result={
                    "content": [{
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, indent=2),
                    }],
                }))
            elif method in ("notifications/initialized", "notifications/cancelled"):
                pass  # no response for notifications
            else:
                write(_make_response(rid, error={
                    "code": -32601, "message": f"method not found: {method}"
                }))
        except Exception as e:
            write(_make_response(rid, error={
                "code": -32603, "message": f"{type(e).__name__}: {e}"
            }))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m pocket_graph.serve <graph.json>", file=sys.stderr)
        sys.exit(1)
    serve(Path(sys.argv[1]))


__all__ = ["serve", "_TOOLS", "_dispatch"]
