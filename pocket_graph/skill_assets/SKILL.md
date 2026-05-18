---
name: pocket-graph
description: Personal knowledge base — ingest sources (URL, keyword, file), build a knowledge graph, write human-readable wiki pages, and query the result. Single integrated skill that handles graph + wiki together. Trigger with /pocket-graph for INGEST, /pocket-graph query for QUERY, /pocket-graph lint for health check, /pocket-graph decisions for structural decisions, /pocket-graph review for revisiting a written page.
trigger: /pocket-graph
---

# /pocket-graph

Manage a personal knowledge base. graph + wiki are written together in a single LLM pass, so they always agree. raw is read **once** per ingest.

## 명령

```
/pocket-graph <url-or-keyword>          # quick ingest (default — auto-write, no questions)
/pocket-graph discuss <url-or-keyword>  # discuss ingest (ask perspective, then write)
/pocket-graph review <slug>             # revisit and refine an existing page
/pocket-graph query <question>          # search wiki+graph; auto-ingest if missing
/pocket-graph lint                      # health check
/pocket-graph decisions [add <title>]   # show or record structural decisions
```

## Step 0a — Verify the CLI is reachable (CRITICAL)

Before doing anything, confirm `pocket_graph` is importable in this Python.
Use a Python one-liner so the check works in any shell (bash, PowerShell, cmd):

```bash
python -c "import pocket_graph"
```

If this exits non-zero, **STOP.** Tell the user:

> "pocket_graph is not installed in this Python. Run: `python -m pip install pocket-graph` (or `pip install ./pocket-graph` from the repo) and try again."

**Do not create vault folders by hand. Do not write graph.json or wiki pages directly.** Manually-created scaffolding without the CLI will produce a broken vault that can't ingest, query, or merge.

## Step 0b — Locate vault root

The vault root is the current working directory unless a config file says otherwise.

Use a Python one-liner so the check is shell-agnostic:

```bash
python -c "from pathlib import Path; import sys; sys.exit(0 if (Path('graph-out').is_dir() or Path('LLM Wiki').is_dir()) else 1)"
```

If this exits non-zero, the cwd is not a pocket-graph vault. Either `cd` to the vault, or run:

```bash
python -m pocket_graph init-vault
```

If `LLM Wiki/_meta/` is missing, run `python -m pocket_graph init-vault` to create the scaffolding before proceeding.

## Routing

Parse the argument after `/pocket-graph`:

- starts with `query` → QUERY flow
- starts with `lint` → LINT flow
- starts with `decisions` → DECISIONS flow
- starts with `review` → REVIEW flow
- starts with `discuss` → DISCUSS INGEST flow
- otherwise → **QUICK INGEST flow** (default — treat the argument as URL or keyword)

If the user says only "ingest <X>", treat it as quick ingest of X.

---

## QUICK INGEST flow (default)

**No questions to user. Auto-detect perspective from the source itself, then write everything in a single LLM pass.**

### Step 1 — Fetch raw

If the argument is a URL (`http://` or `https://`):

```bash
python -m pocket_graph fetch <url> --raw raw/crawled
```

If the argument is a keyword/title:

1. Search the web for the most relevant URL. **Prefer Playwright MCP if available** (browser-quality fetch). Fall back to the built-in `web_search` tool if Playwright MCP is not connected.
2. Show the candidate URL to the user briefly (e.g. "found: https://arxiv.org/abs/1706.03762") and proceed without confirmation. Quick mode = no waiting.
3. `python -m pocket_graph fetch <url> --raw raw/crawled`

If the argument is a local file path: copy it to `raw/files/`.

### Step 2 — Build graph skeleton

```bash
python -m pocket_graph skeleton --out graph-out
```

This runs tree-sitter on the new raw content and produces `graph-out/graph.json` with node/edge structure but **empty descriptions** — descriptions are filled in the same LLM pass as the wiki pages.

### Step 2.5 — Semantic extraction (PDF/이미지/문서 entity 추출)

For PDFs, images, web pages, and any source where tree-sitter can't reach (text-heavy content), extract entity-level nodes and relations directly from the raw file using the same multimodal read in Step 3.

**Use the standard graph JSON schema with the same EXTRACTED/INFERRED/AMBIGUOUS confidence labels as code extraction.**

While reading raw, produce `graph-out/semantic.json`:

```json
{
  "nodes": [
    {
      "id": "<stem>_<entity>",
      "label": "Human Readable Name",
      "file_type": "code|document|paper|image|concept",
      "source_file": "raw/crawled/<file>",
      "source_location": null,
      "source_url": null,
      "captured_at": null,
      "author": null,
      "contributor": null,
      "description": "1-2 sentence summary",
      "tags": ["tag1", "tag2"]
    }
  ],
  "edges": [
    {
      "source": "<node_id>",
      "target": "<node_id>",
      "relation": "<see relation list below>",
      "confidence": "EXTRACTED|INFERRED|AMBIGUOUS",
      "confidence_score": 1.0,
      "source_file": "raw/crawled/<file>",
      "weight": 1.0
    }
  ]
}
```

**Rules**:
- Confidence — `EXTRACTED`: explicit in source. `INFERRED`: reasonable inference. `AMBIGUOUS`: uncertain, flag (don't omit).
- Relations — paper/document: `composed_of`, `extends`, `compared_against`, `evaluated_on`, `references`, `cites`, `replaces`, `conceptually_related_to`, `semantically_similar_to`. Code: `calls`, `implements`, `inherits_from`, `imports`. Don't use `calls`/`implements` for paper edges (they imply runtime, not conceptual composition).
- Node ID — lowercase `[a-z0-9_]`, no dots/slashes. Format: `{stem}_{entity}` (stem = filename without extension).
- **Label ≤ 25 chars, canonical name only.** Parameters, metric values, parentheticals → `description`, not label.
  - Good: `"Transformer (Big)"` / desc: `"213M params; 28.4 BLEU EN-DE."`
  - Bad: `"Transformer Big (213M params, 28.4 BLEU EN-DE)"` (clutters viz).
  - Tables/figures: label = `"Table 3"`, put caption in description.
- **Source root → 2-5 main contributions only** (NOT every concept). For "Attention Is All You Need": root → `transformer`, `self_attention`, `multi_head_attention` (3 edges, not 30+). Linking every concept to root creates a giant hub that drowns concept-concept relationships. Sub-concepts connect to other concepts (`warmup_steps → adam_optimizer`, not → root).
- **Aim 30-45 nodes per paper PDF.** Each node earns its place: referenced multiple times, OR distinctive named technique, OR headline result.
- **Promote to node**: named techniques the paper introduces/compares (Transformer, Multi-Head Attention), datasets/benchmarks (WMT 2014 EN-DE), headline results (BLEU 28.4), architectural variants (Base, Big).
- **Fold into parent description**: hyperparameter values used in one config (`d_model=512`, `dropout=0.1`, `warmup_steps=4000` → inside `transformer_base_config.description`); numbered equations (put inline formula inside parent concept's desc); hardware/training trivia (`8× P100`, `100K steps`); sub-figures restating a concept (Figure 1 architecture → into transformer's desc).
- Tables/figures: keep as nodes only when they carry a distinct argument (Table 1 = complexity claim; Table 3 = ablation). Otherwise fold.
- Don't double-create: `BLEU Score` (metric definition) is one node; specific scores go in result nodes' descriptions.

Then merge:

```bash
python -m pocket_graph apply-semantic graph-out/semantic.json
```

Tree-sitter nodes are preserved. Semantic nodes are added. Edges are deduped by (source, target, relation).

### Step 3 — Single-pass read & write

Read the following files **once**. Do not re-read raw later in the conversation.

- `raw/crawled/<new-file>` (or `raw/files/<new-file>`) — the source
- `graph-out/graph.json` — graph skeleton
- `LLM Wiki/_meta/index.md` — to detect overlap
- `LLM Wiki/_meta/schema.md` — frontmatter rules
- For each domain that the source likely touches, list `LLM Wiki/wiki/<domain>/` to detect overlap candidates

**Reading PDFs**: the native Read tool requires `pdftoppm` (Poppler), which is not installed on Windows by default. If Read fails with `pdftoppm not found`, fall back to `pypdf` (already a base dependency of pocket-graph):

```bash
python -c "from pypdf import PdfReader; print('\n'.join(p.extract_text() for p in PdfReader('raw/crawled/<file>.pdf').pages))"
```

For multi-line Python: write to a temp file and run, or use a heredoc. Do NOT cram multiple statements with `;` inside list comprehensions (it's a SyntaxError — list-comp body must be expressions only).

In **one response** (single LLM turn), generate ALL of the following:

a. **Source page** — `LLM Wiki/wiki/sources/<slug>-source.md`:
```yaml
---
title: <title from source>
type: source
author: <author or empty>
added: <YYYY-MM-DD today>
domain: <primary domain>
source_url: <url or empty>
source_file: raw/crawled/<filename>
status: summarized
---
```
Body: 핵심 주장 요약 + 흥미로운 지점 메모 + `## 관련` section with `[[wikilinks]]` to concept pages.

b. **Concept pages — selective, NOT 1:1 with graph**.

The graph is the catalog of all concepts (30-45 nodes). The wiki holds **5-10 core concept pages only** — the ones worth elaborating beyond a one-line description. Pick using these criteria:
- The paper's main contributions (typically the same nodes the source root is connected to)
- Named techniques the paper introduces (e.g. "Multi-Head Attention", "Layer Normalization")
- Datasets / benchmarks / metrics that may recur in other papers (e.g. "WMT 2014 EN-DE", "BLEU")
- High-degree concepts in the graph (degree ≥ 5, or god-node candidates)

The remaining 25-35 concept nodes are **catalog-only** — they live in `graph-out/graph.json` with rich `description` (1-2 sentences via enrichments), but no `wiki/<domain>/*.md` page. They will be elaborated **on demand** when the user queries them (see QUERY flow). This avoids wasting tokens writing wiki pages the user may never read, while keeping every concept queryable through the graph.

For the 5-10 selected concept pages: full body — frontmatter (`type: concept`, `domain`, `tags`, `perspective: [3-5 fitting perspectives from systems/practitioner/theory/history/interview/math]`, `status: draft`), main definition, perspective-tagged sections, `## 관련` with `[[wikilinks]]`.

**Concept node count** (in graph): paper PDF 30-45; abstract/blog/short doc 10-15; long article 20-30. Each node must earn its place — referenced multiple times, distinctive named technique, or headline result. Over-extraction clutters visualization.

**If a concept page already exists**: update it. Note contradictions with `> [conflict] previous: X / new: Y` blocks.

c. **Graph enrichments** — `graph-out/enrichments.json`:
```json
{
  "<node_id>": {
    "description": "1-2 sentence summary of the node",
    "tags": ["tag1", "tag2"]
  }
}
```

d. **`LLM Wiki/_meta/index.md`** — Read existing content, then Edit to append new entries (link + one-line summary). Do not Write/overwrite — Claude Code's Write tool requires Read first on existing files.

e. **`LLM Wiki/_meta/log.md`** — Read existing content, then Edit to append:
```
## [YYYY-MM-DD] ingest | <source title>
생성/수정한 페이지: <list>
```
**Pattern**: always Read first, then Edit (string-replace mode). Never Write to overwrite existing _meta files — that loses prior content and Claude Code blocks Write-without-Read on existing files anyway.

### Step 4 — Apply enrichments to graph

```bash
python -m pocket_graph apply-enrichments graph-out/enrichments.json --graph graph-out/graph.json
```

This merges descriptions/tags into `graph.json`. Graph and wiki now agree.

### Step 5 — Notify accumulation

If this is the **3rd or later ingest in the same conversation**, tell the user:

> "컨텍스트 누적 비용 방지: 다음 작업 전 `/clear` 권장합니다."

---

## DISCUSS INGEST flow

Same as QUICK INGEST except **between Step 2 and Step 3**:

### Step 2.5 — Discuss with user

Read the raw source (counts as the single read for the whole flow).
Show the user:
- 핵심 주장 3-5개
- Overlap candidates: pages with same/similar title or sharing 3+ tags
  > "Similar page already exists: [[X]] (overlapping tags: [...]). Options: (a) update existing, (b) new sub-topic, (c) merge"
- Available perspectives: `systems | practitioner | theory | history | interview | math` (multiple allowed)
- **Suggested wiki concept pages** (5-10 from the auto-selection criteria — main contributions, named techniques, datasets, high-degree concepts). Show the list, let the user add or drop.

Wait for user response. Then proceed to Step 3 (single-pass write) using the same raw read context — **do not re-read raw**. The graph still gets all 30-45 concept nodes; only the user-confirmed subset becomes wiki pages.

If the discussion resulted in a **structural decision** (merge, split, new domain, new frontmatter field), append to `LLM Wiki/_meta/decisions.md`:
```
## [YYYY-MM-DD]: <decision title>
- **맥락**: <why this came up>
- **결정**: <what was decided>
- **영향**: <what changed>
- **대안**: <alternatives considered>
```

---

## REVIEW flow

`/pocket-graph review <slug>`

Goal: revisit an existing wiki page, possibly promote `draft` → `stable`, fix issues, add cross-references.

1. Read `LLM Wiki/wiki/<domain>/<slug>.md` (or `wiki/sources/<slug>-source.md`).
2. Read the source(s) referenced by the page (from `source_file` frontmatter) **once**.
3. Read related pages (anything linked via `[[]]` or sharing 3+ tags).
4. In a single response:
   - Update content if source has new/clarified info
   - Fix or add `[[wikilinks]]`
   - Update `tags`, `perspective` if needed
   - Update `updated:` to today
   - Promote `status: draft → stable` if content is verified and stable
5. Append to log:
```
## [YYYY-MM-DD] review | <slug>
변경: status draft→stable, 추가 링크 N개, ...
```

---

## QUERY flow

`/pocket-graph query <question>`

### Step 1 — BFS traversal

```bash
python -m pocket_graph query "<question>" --depth 3 --budget 2000
```

This runs BFS:
1. Tokenize question into terms (>2 chars)
2. Score every node by term matches in label / source / tags / description
3. Take top 3 as seed nodes
4. BFS depth=3 from seeds
5. Optionally filter edges by context (call / import / field / parameter_type / return_type / generic_arg) — auto-inferred from the question
6. Returns subgraph as text within token budget

The output text lists NODE entries (with id, label, src, tags, description) and EDGE entries (with relation + confidence). This is the navigator — tells you which wiki pages and raw files to read.

### Step 2 — Read by status

For each candidate node found in traversal, check if a corresponding wiki page exists in `LLM Wiki/wiki/`:

- **wiki exists AND `status: stable`** → cite as the **preferred source** ("according to [[link]], X is Y"). User has reviewed the page, but answers should still be calibrated to the source — don't add false confidence. If raw also exists, stable wiki is the first place to look; reach for raw only if wiki doesn't cover the question.
- **wiki exists AND `status: draft`** → use with hedged language ("a draft note suggests X, though this needs verification"). Sources may be incomplete or unverified.
- **wiki exists AND `status: archived`** → skip in answer; inform user the page is archived (deprecated/historical content). Do not cite. If it is the only relevant page, surface this to the user instead of using it.
- **wiki does not exist (graph node only)** → use the graph node's `description` field as the first answer source. If more depth is needed, read the raw file from `source_file`. Track which concepts triggered raw reads — these are wiki-creation candidates for Step 3.

### Step 3 — Answer + offer wiki page creation

Synthesize and answer the user's question using whatever sources Step 2 surfaced.

**At the end of the answer**, if any concept was elaborated by reading raw (no wiki page existed), batch-ask the user:

> "다음 concept들에 대한 wiki 페이지를 만들까요? `[concept-a, concept-b, ...]` (yes / no / specify which / all / none)"

- User says **yes / all** → run inline write for each (full wiki page, status: `draft`, perspectives auto-detected). Update `_meta/index.md` and append to log.
- User says **no / none** → leave them as graph-only nodes. They can be created later via QUERY again or `/pocket-graph review <slug>`.
- User says **specify which** → create only the named subset.

**If `python -m pocket_graph query` returns "No matching nodes found"** (true knowledge gap):
- Search the web for the most relevant source (Playwright MCP preferred, fallback web_search).
- Run QUICK INGEST on that URL (which itself uses the selective 5-10 wiki pattern).
- Answer using the freshly ingested wiki + graph.

Append to log:
```
## [YYYY-MM-DD] query | <question summary>
답변 저장 위치: <wiki page if any created>
auto-ingested: <url if any>
created wiki on demand: <list of slugs if any>
```


### Optional: deeper / narrower searches

If the BFS returned too many irrelevant nodes:
- `python -m pocket_graph query "<q>" --mode dfs --depth 5` — trace specific paths
- `python -m pocket_graph query "<q>" --context-filter call` — only call edges (find function callers)
- `python -m pocket_graph query "<q>" --budget 4000` — wider context

---

## LINT flow

`/pocket-graph lint`

Check wiki health, in this order:

1. **Index/file mismatch** — `_meta/index.md` vs actual `wiki/` files (missing or stale entries).
2. **Broken outbound links** — `[[wikilinks]]` whose target file doesn't exist.
3. **Semantic overlap** — concept pages within a domain sharing 3+ tags → merge/cross-ref candidates.
4. **Inbound orphans** — pages no other page links to.
5. **Stale drafts** — `status: draft` not updated for >30 days.
6. **Unlinked mentions** — concept titles/aliases appearing in other pages as plain text (not `[[...]]`).
7. **Contradictions** — conflicting statements within a domain (best effort).
8. **Data gaps** — suggest new questions / sources to investigate.

Append to log:
```
## [YYYY-MM-DD] lint
발견한 문제: <summary>
제안된 다음 소스: <list>
```

If lint produces a structural recommendation the user accepts, append to `_meta/decisions.md`.

---

## DECISIONS flow

`/pocket-graph decisions` — read `_meta/decisions.md`, list all decisions reverse-chronologically (date + title + one-line summary). Ask if user wants to record/review.

`/pocket-graph decisions add <title>` — interactively gather **맥락** (왜 필요), **결정** (무엇을), **영향** (변화), **대안** (선택). Append to `_meta/decisions.md`:
```
## [YYYY-MM-DD]: <title>
- **맥락**: ...
- **결정**: ...
- **영향**: ...
- **대안**: ...
```

---

## Rules — never break these

1. **raw 파일 수정 금지** (`raw/files/`, `raw/crawled/`)
2. **graph-out/ 직접 편집 금지** — `python -m pocket_graph apply-enrichments` 사용
3. **`[[wikilinks]]`는 frontmatter 금지** — 본문 `## 관련` section만
4. **단일 turn에서 graph + wiki 동시 작성** — raw 두 번 read 금지 (1M context로 충분)
5. **누적 컨텍스트 경고** — 같은 세션 3번째 ingest부터 `/clear` 권장
6. **discuss 모드에서만 사용자에게 perspective 질문** — quick은 자동 판단
7. **wiki는 selective** (5-10 core concepts per source). 모든 graph 노드를 wiki로 만들지 말 것 — graph는 catalog, wiki는 elaboration. 나머지는 query 시 사용자 동의 받고 on-demand 생성.
8. **archived status 신규 권장 안 함** — query에서 사용 안 됨. deprecated/historical 보존용으로만.

---

## Error handling

- `graph-out/graph.json` 없음 → 사용자에게 `python -m pocket_graph ingest <source>` 또는 `python -m pocket_graph init-vault` 안내
- `LLM Wiki/_meta/` 없음 → `python -m pocket_graph init-vault` 자동 실행
- fetch 실패 → 사용자에게 원인 알리고 중단
- Playwright MCP 호출 실패 → web_search로 fallback, 사용자에게 알림
