# pocket-graph

*English: [README.md](./README.md)*

[Claude Code](https://github.com/anthropics/claude-code)를 위한 개인용 knowledge-graph + wiki 도구. 논문, 글, 코드를 그래프로 ingest하고 자동 생성된 wiki vault로 질의할 수 있습니다. Safi Shamsi의 [graphify](https://github.com/safishamsi/graphify) (MIT)를 참고하여 제작했습니다 — 제3자 attribution은 [`LICENSE`](./LICENSE) 참고.

## pocket-graph가 추가한 것

- **Single-turn ingest** — graph + wiki를 한 번의 LLM pass로 작성. raw는 한 번만 읽음.
- **pocket-wiki vault 레이아웃** — `LLM Wiki/wiki/{sources,domain}/`, `_meta/{index,log,decisions,schema}.md`, frontmatter (`status: draft|stable|archived`, `perspective`, `tags`).
- **한글 / multi-byte safe** — 모든 파일 쓰기에 명시적 UTF-8, Windows cp949 환경에서 stdout reconfigure.
- **`apply-semantic` / `apply-enrichments`** — LLM-extracted entity merge를 tree-sitter skeleton과 분리. SKILL이 raw를 두 번 읽지 않고도 graph + wiki를 함께 작성 가능.
- **`init-vault`** — 새 paper/wiki vault scaffolding.
- **BFS scoring에서 tag/description 매칭** — graphify의 스코어링은 `label`과 `source_file`을 사용. pocket-graph는 여기에 `tags` (+2 per term)와 `description` (+0.3 per term)을 추가로 가중해 docs/papers에 대한 recall을 강화.

## 설치

Python 3.10+ 필요.

```bash
git clone https://github.com/alias26/pocket-graph
cd pocket-graph
pip install .
```

`pip install`은 post-install hook을 통해 `SKILL.md`를 `~/.claude/skills/pocket-graph/`로 복사합니다. 그래서 모든 Claude Code 세션에서 프로젝트별 셋업 없이 slash command가 동작합니다.

Windows에서는 hook이 `PYTHONUTF8=1`도 자동 설정합니다 (User scope) — Python 표준 UTF-8 mode (PEP 540). Greek letter나 em dash가 들어간 PDF를 읽을 때의 `UnicodeEncodeError` (cp949)를 피하기 위함입니다. 설치 후 새로 여는 셸부터 적용됩니다. 되돌리려면 `setx PYTHONUTF8 ""`.

설치 후 `pocket-graph`가 PATH에 없으면 `python -m pocket_graph`로 호출해도 됩니다. 번들된 SKILL은 처음부터 그렇게 호출합니다.

선택 extras:

```bash
pip install '.[watch]'     # 파일 워처 (watchdog)
pip install '.[mcp]'       # Claude Desktop용 MCP server
pip install '.[languages]' # 24개 언어의 tree-sitter parser
pip install '.[office]'    # .docx, .xlsx 파싱
pip install '.[pdf]'       # PyMuPDF (pocket-graph 자체 extract_pdf CLI를 쓸 때만)
```

`pypdf`는 (SKILL이 PDF를 ingest할 때 Claude Code의 read tool이 내부적으로 사용) base install에 포함되어 있습니다 — arXiv 논문을 `/pocket-graph <arxiv-url>`로 ingest하기엔 `pip install .`만으로 충분합니다.

## Quick start

```bash
mkdir ~/my-vault && cd ~/my-vault
pocket-graph init-vault
```

이후 Claude Code (cwd = `~/my-vault`)에서:

```
/pocket-graph https://arxiv.org/abs/1706.03762
```

SKILL이 fetch → tree-sitter skeleton → single-turn LLM read → graph + wiki write → apply-semantic → apply-enrichments까지 한 turn 안에서 처리합니다.

## 명령

### 사용자용

| 명령 | 목적 |
|---|---|
| `init-vault` | vault scaffolding 생성 (`raw/`, `LLM Wiki/`, `_meta/`) |
| `ingest <url\|path\|keyword>` | corpus나 URL로부터 graph 빌드 |
| `update` | 증분 재빌드 (변경 파일만) |
| `query "<question>"` | BFS/DFS traversal, subgraph를 텍스트로 반환 |
| `path <src> <tgt>` | 두 노드 사이 최단 경로 |
| `explain <node>` | 노드 + 이웃 + 관계 |
| `clone <github-url>` | `~/.pocket-graph/repos/<owner>/<repo>`로 git-clone |
| `check-update [path]` | Cron-safe: 마지막 빌드 이후 변경 파일 탐지 |
| `merge-graphs <g1> <g2> ...` | 여러 graph.json을 병합 (cross-vault / cross-repo) |
| `tree` | D3 v7 collapsible-tree HTML 뷰 |
| `watch [path]` | 파일 변경 시 자동 재빌드 (`[watch]` extra 필요) |
| `export --formats <fmts>` | obsidian / cypher / graphml / svg / html로 재export |
| `serve` | stdio 기반 MCP server (`[mcp]` extra 필요) |
| `stats` | 지원 언어 목록 |

### SKILL 내부용 (`SKILL.md`에서 호출)

| 명령 | 목적 |
|---|---|
| `fetch <url>` | `raw/`로 fetch, graph 빌드는 안 함 (arXiv 자동 감지 → 주석 달린 markdown) |
| `skeleton` | tree-sitter 그래프만, LLM 없음 |
| `apply-semantic <file>` | SKILL이 추출한 entity node/edge를 `graph.json`에 merge |
| `apply-enrichments <file>` | `{node_id: {description, tags}}`를 `graph.json`에 merge |

## Vault 레이아웃

```
~/my-vault/
├── raw/
│   ├── crawled/          # `fetch <url>` 출력
│   └── files/            # 로컬 파일을 떨궈 두는 곳
├── graph-out/
│   ├── graph.json        # 그래프
│   ├── graph.html        # force-directed 뷰
│   ├── tree.html         # collapsible-tree 뷰 (`pocket-graph tree`)
│   ├── GRAPH_REPORT.md   # 자동 생성 하이라이트
│   ├── analysis.json     # god node, community 등
│   └── manifest.json     # 증분 업데이트용 SHA256 캐시
└── LLM Wiki/
    ├── wiki/
    │   ├── sources/<slug>-source.md      # paper/article 요약
    │   └── <domain>/<concept>.md         # 개념 페이지 (status: draft|stable|archived)
    └── _meta/
        ├── index.md       # 자동 생성 카탈로그
        ├── log.md         # ingest/query/lint 로그 (append-only)
        ├── decisions.md   # ADR 스타일 구조적 결정
        └── schema.md      # frontmatter 규칙
```

`raw/`와 `graph-out/`은 CLI가 관리합니다 — 직접 편집 금지. Wiki 파일은 Claude Code의 SKILL이 관리하지만 직접 편집해도 됩니다. `_meta/`만은 도구에 맡기는 게 좋습니다.

## 문서

- [`docs/SCHEMA_ko.md`](./docs/SCHEMA_ko.md) — graph node/edge 스키마 레퍼런스.
- [`docs/SKILL_USAGE_ko.md`](./docs/SKILL_USAGE_ko.md) — Claude Code 내부에서 SKILL이 CLI를 어떻게 구동하는지, MCP server 도구 포함.

영문 버전: [`docs/SCHEMA.md`](./docs/SCHEMA.md), [`docs/SKILL_USAGE.md`](./docs/SKILL_USAGE.md).

## License

MIT. [`LICENSE`](./LICENSE) 참고 — 파일 하단에 graphify(MIT) 제3자 attribution 포함.
