# SKILL Usage

> 한국어 버전: [SKILL_USAGE_ko.md](./SKILL_USAGE_ko.md)

How the pocket-graph SKILL is invoked from Claude Code and how it queries the graph. Install is covered in the [main README](../README.md).

## When the SKILL fires

The bundled `SKILL.md` activates on questions that need graph traversal:

- **Relation questions** — "what calls X", "path between X and Y", "all `@classmethod` functions", "members of class X"
- **Structural questions** — "nodes that bridge two communities", "hub of class Index"
- **Evolution questions** — "what changed from yesterday's graph"

## When NOT to use the graph

- Free-text summarisation ("how does scoring work") → read the file directly.
- Exact values inside node bodies ("what timeout does this function use?") → the graph stores location, not body; fetch separately.
- Small corpora (< 10 files) → `grep` is faster.

## 4-step query workflow

The SKILL internalises this flow whenever it answers a graph question:

### Step 1 — IDENTIFY_NODES

Map entities mentioned in the question to node IDs.

```
User: "What calls Index.lookup?"
Claude: find_nodes(G, "lookup") → [{"id": "index_index_lookup", ...}]
```

### Step 2 — CHOOSE_TRAVERSAL

| Question shape | Tool |
|---|---|
| "what X calls" | `callees_of(node_id)` |
| "what calls X" | `callers_of(node_id)` |
| "path from A to B" | `shortest_path(src, tgt)` |
| "1–2 hops around X" | `get_neighbors(node_id, depth)` |
| "uses of `@decorator`" | `filter_by_decorator(decorator)` |
| "all `T` relations" | `filter_by_relation(relation)` |
| "group X belongs to" | `find_hyperedges_for(node_id)` |
| "list all groups" | `list_hyperedges(type)` |
| "bridge between two communities" | `bridging_nodes(he_a, he_b)` |
| "hub of group X" | `centrality_within(he_id)` |
| "diff two graph snapshots" | `evolution_diff(old, new)` |

### Step 3 — EXECUTE

Run the tool. One call is usually enough.

### Step 4 — ANSWER_FROM_GRAPH

Answer from graph results alone. **Only fetch source bodies via the returned `source_file:source_location` when the question demands the actual code.**

## Token efficiency

| Question type | grep | read-files | graph |
|---|---:|---:|---:|
| Plain text match | ~100 | 1000+ | 500–1300 |
| Relation traversal (callers, path) | can't answer | 8000+ | 300–500 |
| Hyperedge (community members) | can't answer | 12000+ | 300 |

Average: the graph saves **~88 % vs. read-files**, costs similar to `grep`, and answers a much wider range of questions.

## MCP server

Expose the graph as MCP tools to Claude Code or any MCP client:

```bash
pocket-graph serve ./graph-out/graph.json
```

JSON-RPC 2.0 over stdio. 13 tools exposed:

`find_nodes`, `get_node`, `get_neighbors`, `shortest_path`, `filter_by_relation`, `filter_by_decorator`, `callees_of`, `callers_of`, `find_hyperedges_for`, `list_hyperedges`, `bridging_nodes`, `centrality_within`, `evolution_diff`.

Example Claude Code MCP config:

```json
{
  "mcpServers": {
    "pocket-graph": {
      "command": "pocket-graph",
      "args": ["serve", "/path/to/your-repo/graph-out/graph.json"]
    }
  }
}
```

(Exact config key may differ by Claude Code version — check your docs.)

## Example dialog

```
User: How do we get from SearchPipeline.query to Index.lookup?

Claude (running SKILL):
  1. find_nodes("SearchPipeline.query") → pipeline_searchpipeline_query
  2. find_nodes("Index.lookup")         → index_index_lookup
  3. shortest_path(...)
  Result: query() → top_k() → score() → lookup()   (length 3)

Answer: "query() goes through BM25Ranker.top_k(), then score() inside
ranker.py:21 makes a cross-class call to Index.lookup()."
```
