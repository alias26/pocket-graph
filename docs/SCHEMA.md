# Graph Schema

> 한국어 버전: [SCHEMA_ko.md](./SCHEMA_ko.md)

pocket-graph follows the graphify v6 schema so graphs are interchangeable between the two. Graphs are stored as NetworkX node-link JSON.

## Top-level structure

```json
{
  "directed": true,
  "graph": {
    "hyperedges": [...]
  },
  "nodes": [...],
  "links": [...]
}
```

## Node

```json
{
  "id": "index_index_lookup",
  "label": "lookup()",
  "file_type": "code",
  "source_file": "index.py",
  "source_location": "L42",
  "community": 3,
  "weight": 1
}
```

### Fields

- `id` — stable identifier. Format: `<file_stem>_<class>_<method>` or `<file_stem>_<func>`. Joined by underscore.
- `label` — human-readable name. Methods/functions end with `()`, classes are PascalCase, decorators use `@xxx`.
- `file_type`:
  - `code` — function, class, method, decorator, etc.
  - `document` — Markdown heading, docx paragraph, etc.
  - `paper` — PDF section
  - `image` — image file (placeholder, OCR not implemented)
  - `rationale` — Python docstring / NOTE / WHY / HACK comment
  - `concept` — explicit concept node (rare)
- `source_file` — relative path
- `source_location` — `L<line_number>` format
- `community` — Louvain community id (0-based). Used for god-node / hub analysis.
- `weight` — sum of edge weights (call / import frequency)

## Edge (link)

```json
{
  "source": "ranker_bm25ranker_score",
  "target": "index_index_lookup",
  "relation": "calls",
  "confidence": "INFERRED",
  "source_file": "ranker.py",
  "source_location": "L21",
  "weight": 1,
  "call_sites": ["L21"]
}
```

### `relation` (14 kinds)

| Relation | Meaning |
|---|---|
| `defines` | File hub → top-level function/class |
| `contains` | Class → method, module → class |
| `calls` | Function → callee function |
| `imports` | Module → imported module |
| `imports_from` | Module → specific symbol from a module |
| `uses` | Function/class → decorator |
| `uses_component` | (JSX/Svelte) component → component |
| `uses_static_prop` | Static property reference |
| `references_constant` | Constant reference |
| `binds_method` | (Event handler) method binding |
| `bound_to` | Method ← binding target |
| `includes` | (HTML/template) include |
| `listened_by` | Event → listener |
| `rationale_for` | Docstring/comment → the node it explains |

### `confidence` (3 levels)

| Level | Meaning |
|---|---|
| `EXTRACTED` | Directly from source — highest trust (structural relations like file → defines → function) |
| `INFERRED` | Resolved cross-file in Pass 2. Single user-defined match. |
| `AMBIGUOUS` | Ambiguous — stdlib name collision or same name in different classes. **Review recommended.** |

## Hyperedge

`G.graph["hyperedges"]` is a list of N-node groups — N-ary relations that can't be expressed as ordinary edges.

```json
{
  "id": "hg_class_index_index",
  "type": "class_group",
  "label": "class Index",
  "members": ["index_index", "index_index_lookup", "index_index_add", ...],
  "size": 9,
  "source_file": "index.py"
}
```

### Two kinds

- `class_group` — a class plus its methods. Derived from `contains` edges.
- `community` — Louvain community. Semantic grouping.

## Special node ID conventions

- File hub: `<file_stem>` (e.g. `index`)
- Module-level function: `<file_stem>_<func_name>` (e.g. `extract_extract_file`)
- Class: `<file_stem>_<class_name_lowercase>` (e.g. `index_index`)
- Method: `<file_stem>_<class_name_lowercase>_<method_name>` (e.g. `index_index_lookup`)
- Decorator: `decorator_<dec_name>` (global — shared across all files)
- Rationale: `<source_file_stem>_docstring_<line>`
