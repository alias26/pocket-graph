# pocket-graph

*한국어: [README_ko.md](./README_ko.md)*

Personal knowledge-graph + wiki tool for [Claude Code](https://github.com/anthropics/claude-code). Ingest papers, articles, and code into a queryable graph and an auto-generated wiki vault. Developed with reference to [graphify](https://github.com/safishamsi/graphify) by Safi Shamsi (MIT) — see [`LICENSE`](./LICENSE) for third-party notices.

## What pocket-graph adds

- **Single-turn ingest** — graph + wiki written in one LLM pass; raw read once.
- **pocket-wiki vault layout** — `LLM Wiki/wiki/{sources,domain}/`, `_meta/{index,log,decisions,schema}.md`, frontmatter (`status: draft|stable|archived`, `perspective`, `tags`).
- **Korean / multi-byte safe** — explicit UTF-8 on every file write, stdout reconfigure on Windows cp949 environments.
- **`apply-semantic` / `apply-enrichments`** — separate the LLM-extracted entity merge from the tree-sitter skeleton, so the SKILL can write graph + wiki together without a second raw read.
- **`init-vault`** — scaffolding for a fresh paper/wiki vault.
- **Tag/description matching in BFS scoring** — graphify's scoring uses `label` and `source_file`; pocket-graph also weighs `tags` (+2 per term) and `description` (+0.3 per term) for richer recall on docs/papers.

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/alias26/pocket-graph
cd pocket-graph
pip install .
```

`pip install` runs a post-install hook that copies `SKILL.md` to `~/.claude/skills/pocket-graph/`, so the slash command works in every Claude Code session without per-project setup.

On Windows, the hook also sets `PYTHONUTF8=1` automatically (User scope) — Python's standard UTF-8 mode (PEP 540). This avoids `UnicodeEncodeError` (cp949) when reading paper PDFs with Greek letters or em dashes. Effect takes hold in shells you open after install. Undo with `setx PYTHONUTF8 ""` if you prefer cp949.

If `pocket-graph` isn't on your PATH after install, use `python -m pocket_graph` everywhere. The bundled SKILL already calls it that way.

Optional extras:

```bash
pip install '.[watch]'     # file-watcher (watchdog)
pip install '.[mcp]'       # MCP server for Claude Desktop
pip install '.[languages]' # tree-sitter parsers for 24 languages
pip install '.[office]'    # .docx, .xlsx parsing
pip install '.[pdf]'       # PyMuPDF (only if you call pocket-graph's own extract_pdf CLI)
```

`pypdf` (used by Claude Code's read tool when the SKILL ingests a PDF) is in the base install — `pip install .` is enough to ingest arXiv papers via `/pocket-graph <arxiv-url>`.

## Quick start

```bash
mkdir ~/my-vault && cd ~/my-vault
pocket-graph init-vault
```

Then in Claude Code (cwd = `~/my-vault`):

```
/pocket-graph https://arxiv.org/abs/1706.03762
```

The SKILL handles fetch → tree-sitter skeleton → single-turn LLM read → graph + wiki write → apply-semantic → apply-enrichments, all in one Claude Code turn.

## Commands

### User-facing

| Command | Purpose |
|---|---|
| `init-vault` | Create fresh vault scaffolding (`raw/`, `LLM Wiki/`, `_meta/`) |
| `ingest <url\|path\|keyword>` | Build graph from a corpus or URL |
| `update` | Incremental rebuild (changed files only) |
| `query "<question>"` | BFS/DFS traversal, returns subgraph as text |
| `path <src> <tgt>` | Shortest path between two nodes |
| `explain <node>` | Node + neighbours + relations |
| `clone <github-url>` | git-clone a repo to `~/.pocket-graph/repos/<owner>/<repo>` |
| `check-update [path]` | Cron-safe: detect changed files since last build |
| `merge-graphs <g1> <g2> ...` | Merge multiple graph.json files (cross-vault / cross-repo) |
| `tree` | D3 v7 collapsible-tree HTML view |
| `watch [path]` | Auto-rebuild on file changes (needs `[watch]` extra) |
| `export --formats <fmts>` | Re-export to obsidian / cypher / graphml / svg / html |
| `serve` | MCP server over stdio (needs `[mcp]` extra) |
| `stats` | List supported languages |

### SKILL-internal (called from `SKILL.md`)

| Command | Purpose |
|---|---|
| `fetch <url>` | Fetch into `raw/`, no graph build (auto-detects arXiv → annotated markdown) |
| `skeleton` | tree-sitter graph only, no LLM |
| `apply-semantic <file>` | Merge SKILL-extracted entity nodes/edges into `graph.json` |
| `apply-enrichments <file>` | Merge `{node_id: {description, tags}}` into `graph.json` |

## Vault layout

```
~/my-vault/
├── raw/
│   ├── crawled/          # `fetch <url>` output
│   └── files/            # local files you drop in
├── graph-out/
│   ├── graph.json        # the graph
│   ├── graph.html        # force-directed view
│   ├── tree.html         # collapsible-tree view (`pocket-graph tree`)
│   ├── GRAPH_REPORT.md   # auto-generated highlights
│   ├── analysis.json     # god nodes, communities, etc.
│   └── manifest.json     # SHA256 cache for incremental update
└── LLM Wiki/
    ├── wiki/
    │   ├── sources/<slug>-source.md      # paper/article summary
    │   └── <domain>/<concept>.md         # concept pages (status: draft|stable|archived)
    └── _meta/
        ├── index.md       # auto-generated catalogue
        ├── log.md         # ingest/query/lint log (append-only)
        ├── decisions.md   # ADR-style structural decisions
        └── schema.md      # frontmatter rules
```

`raw/` and `graph-out/` are managed by the CLI — don't hand-edit. Wiki files are managed by the SKILL through Claude Code; you can hand-edit them too, but `_meta/` is best left to the tool.

## Documentation

- [`docs/SCHEMA.md`](./docs/SCHEMA.md) — graph node/edge schema reference.
- [`docs/SKILL_USAGE.md`](./docs/SKILL_USAGE.md) — how the SKILL drives the CLI from inside Claude Code, including the MCP server tools.

Korean versions: [`docs/SCHEMA_ko.md`](./docs/SCHEMA_ko.md), [`docs/SKILL_USAGE_ko.md`](./docs/SKILL_USAGE_ko.md).

## License

MIT. See [`LICENSE`](./LICENSE) — includes third-party notices for graphify (MIT) at the bottom.
