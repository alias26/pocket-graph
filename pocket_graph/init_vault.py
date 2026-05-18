"""Initialize a pocket-graph vault structure.

Creates the pocket-wiki layout in the current directory:

    <cwd>/
    ├── raw/
    │   ├── files/
    │   └── crawled/
    ├── graph-out/                  (created on first ingest)
    └── LLM Wiki/
        ├── wiki/
        │   ├── sources/
        │   └── (domains created as needed)
        └── _meta/
            ├── index.md
            ├── log.md
            ├── decisions.md
            └── schema.md

Idempotent: existing files are not overwritten.
"""
from __future__ import annotations
from pathlib import Path
from datetime import date


META_INDEX = """# Wiki Index

이 파일은 ingest될 때마다 자동 갱신된다.

## Sources

(아직 없음)

## Concepts

(아직 없음)
"""

META_LOG = """# Activity Log

ingest, query, lint 작업 기록 (append-only).

"""

META_DECISIONS = """# Structural Decisions

wiki 구조 결정 기록 (ADR 스타일). 형식은 `_meta/schema.md` 참조.

"""

META_SCHEMA = """# Frontmatter Schema

## source 페이지 (`wiki/sources/<slug>-source.md`)

```yaml
---
title:
type: source
author:
added: YYYY-MM-DD
domain:
source_url:
source_file:
status: summarized
---
```

## concept 페이지 (`wiki/<domain>/<slug>.md`)

```yaml
---
title:
type: concept
domain:
tags: []
perspective: []   # systems | practitioner | theory | history | interview | math
updated: YYYY-MM-DD
status: draft | stable | archived
---
```

## 규칙

- `[[wikilinks]]`는 frontmatter에 절대 두지 않는다. 본문 `## 관련` 섹션에만.
- status 의미:
  - `summarized` -- source 페이지 (요약 완료)
  - `draft` -- concept 페이지 초안 (오류 가능, query 시 hedged language로 인용)
  - `stable` -- 사용자 검증 완료. query 시 우선 인용 (단정적 인용 X — 검증 시점과 query 시점 차이, 빠진 부분 가능성 있음)
  - `archived` -- deprecated 또는 과거 내용. **신규 페이지에는 권장하지 않음**. query 결과에 사용 안 됨. 사용 케이스: 이전 결정/구버전을 history로 보존할 때만.
- perspective 다중 선택 가능 (e.g. `[theory, math]`)
- domain은 폴더 이름과 일치해야 함

## decisions.md ADR 포맷

```
## [실제 날짜, 예: 2026-05-07]: 결정 제목
- **맥락**: 이 결정이 왜 필요했나
- **결정**: 무엇을 어떻게 하기로 했나
- **영향**: 기존 페이지/워크플로우에 어떤 변화
- **대안**: 고려했다가 기각한 방법 (있을 때만)
```
"""


def init_vault(root: Path | None = None, verbose: bool = True) -> dict:
    """Create pocket-graph vault scaffolding under `root` (default: cwd).

    Returns a dict of {path: action} where action is 'created' or 'exists'.
    """
    root = root or Path.cwd()
    today = date.today().isoformat()

    actions: dict[str, str] = {}

    # Directories
    dirs = [
        root / "raw" / "files",
        root / "raw" / "crawled",
        root / "LLM Wiki" / "wiki" / "sources",
        root / "LLM Wiki" / "_meta",
    ]
    for d in dirs:
        if d.exists():
            actions[str(d)] = "exists"
        else:
            d.mkdir(parents=True, exist_ok=True)
            actions[str(d)] = "created"

    # _meta files
    meta_files = {
        root / "LLM Wiki" / "_meta" / "index.md": META_INDEX,
        root / "LLM Wiki" / "_meta" / "log.md": META_LOG,
        root / "LLM Wiki" / "_meta" / "decisions.md": META_DECISIONS,
        root / "LLM Wiki" / "_meta" / "schema.md": META_SCHEMA,
    }
    for path, content in meta_files.items():
        if path.exists():
            actions[str(path)] = "exists"
        else:
            path.write_text(content, encoding="utf-8")
            actions[str(path)] = "created"

    if verbose:
        for path, action in actions.items():
            tag = "[+] created" if action == "created" else "[ ] exists "
            print(f"  {tag}  {path}")

    return actions


__all__ = ["init_vault"]
