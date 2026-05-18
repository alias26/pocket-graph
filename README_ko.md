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

```bash
git clone https://github.com/alias26/pocket-graph
cd pocket-graph
pip install .
```

Post-install hook이 `SKILL.md`를 `~/.claude/skills/pocket-graph/`로 복사하고, Windows에서는 `PYTHONUTF8=1`을 자동 설정합니다.

### Optional extras

```bash
pip install '.[watch]'      # 파일 워처 (watchdog)
pip install '.[mcp]'        # Claude Desktop용 MCP server
pip install '.[languages]'  # 24개 언어의 tree-sitter parser
pip install '.[office]'     # .docx, .xlsx 파싱
pip install '.[pdf]'        # PyMuPDF (extract_pdf CLI 전용)
```

### Platform notes

- **Windows UTF-8** — 설치 스크립트가 `PYTHONUTF8=1` (PEP 540)을 자동 설정해, cp949 환경에서 Greek letter나 em dash가 들어간 PDF를 읽을 때 발생하는 `UnicodeEncodeError`를 방지합니다. 설치 후 새로 여는 셸부터 적용됩니다. 되돌리려면 `setx PYTHONUTF8 ""`를 실행하세요.
- **PATH fallback** — 설치 후 `pocket-graph`가 PATH에 등록되지 않았다면 `python -m pocket_graph`로 호출하세요.
- **컨텍스트 크기** — 논문 ingest 시에는 1M 컨텍스트 모델(예: Sonnet 4.6 [1m]) 사용을 권장합니다. 200K 윈도우는 긴 논문에서 빠듯할 수 있습니다.

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

SKILL 내부 명령(`fetch`, `skeleton`, `apply-semantic`, `apply-enrichments`)은 `SKILL.md`가 자동 호출 — 자세한 내용은 [`docs/SKILL_USAGE_ko.md`](./docs/SKILL_USAGE_ko.md) 또는 `pocket-graph --help` 참고.

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

- **`raw/`** — 소스 파일이 모이는 영역입니다. 로컬 파일은 `raw/files/`에 직접 떨궈도 되고, `fetch` 명령은 다운로드한 내용을 `raw/crawled/`에 기록합니다.
- **`graph-out/`** — CLI가 생성하며 ingest할 때마다 덮어쓰므로 내부의 어떤 파일도 직접 편집하지 마세요.
- **`LLM Wiki/wiki/`** — concept 페이지와 source 요약이 들어가는 곳이며, 직접 편집해도 안전합니다.
- **`LLM Wiki/_meta/`** — SKILL이 관리하는 영역(index, log, decisions, schema)이므로 직접 편집하지 마세요.

## 문서

- [`docs/SCHEMA_ko.md`](./docs/SCHEMA_ko.md) — graph node/edge 스키마 레퍼런스.
- [`docs/SKILL_USAGE_ko.md`](./docs/SKILL_USAGE_ko.md) — Claude Code 내부에서 SKILL이 CLI를 어떻게 구동하는지, MCP server 도구 포함.

영문 버전: [`docs/SCHEMA.md`](./docs/SCHEMA.md), [`docs/SKILL_USAGE.md`](./docs/SKILL_USAGE.md).

## License

MIT. [`LICENSE`](./LICENSE) 참고 — 파일 하단에 graphify(MIT) 제3자 attribution 포함.
