# 그래프 스키마

> English: [SCHEMA.md](./SCHEMA.md)

pocket-graph는 graphify v6 schema를 따르므로 두 도구 사이에 그래프를 호환되게 주고받을 수 있습니다. 그래프는 NetworkX node-link JSON으로 저장됩니다.

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

- `id` — 안정 식별자. 형식: `<file_stem>_<class>_<method>` 또는 `<file_stem>_<func>`. underscore로 join.
- `label` — 사람이 읽을 수 있는 이름. Method/function은 `()`가 붙고, 클래스는 PascalCase, 데코레이터는 `@xxx` 형식.
- `file_type`:
  - `code` — 함수, 클래스, 메서드, 데코레이터 등
  - `document` — Markdown heading, docx paragraph 등
  - `paper` — PDF section
  - `image` — 이미지 파일 (placeholder, OCR 미구현)
  - `rationale` — Python docstring / NOTE / WHY / HACK 주석
  - `concept` — 명시적 concept 노드 (드물게 등장)
- `source_file` — 상대 경로
- `source_location` — `L<line_number>` 형식
- `community` — Louvain community id (0부터). god node / hub 분석에 사용.
- `weight` — 엣지 weight 합 (호출/import 빈도)

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

### `relation` (14가지)

| Relation | 의미 |
|---|---|
| `defines` | File hub → top-level function/class |
| `contains` | Class → method, module → class |
| `calls` | Function → callee function |
| `imports` | Module → imported module |
| `imports_from` | Module → 모듈 내 특정 심볼 |
| `uses` | Function/class → decorator |
| `uses_component` | (JSX/Svelte) component → component |
| `uses_static_prop` | 정적 프로퍼티 참조 |
| `references_constant` | 상수 참조 |
| `binds_method` | (이벤트 핸들러) method binding |
| `bound_to` | Method ← binding target |
| `includes` | (HTML/template) include |
| `listened_by` | Event → listener |
| `rationale_for` | Docstring/comment → 설명 대상 노드 |

### `confidence` (3가지)

| Level | 의미 |
|---|---|
| `EXTRACTED` | 소스에 그대로 — 가장 신뢰 (file → defines → function 같은 구조적 관계) |
| `INFERRED` | Pass 2가 cross-file로 resolve. 단일 user-defined 매치 |
| `AMBIGUOUS` | 모호 — stdlib 이름 충돌이나 같은 이름 다른 클래스. **review 권장** |

## Hyperedge

`G.graph["hyperedges"]`는 N-노드 그룹의 list. 일반 edge로 표현할 수 없는 N-ary 관계를 담습니다.

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

### 두 종류

- `class_group` — 클래스 + 그 메서드들. `contains` edge로부터 derive.
- `community` — Louvain community. 의미적 그룹화.

## Special node ID conventions

- File hub: `<file_stem>` (예: `index`)
- Module-level function: `<file_stem>_<func_name>` (예: `extract_extract_file`)
- Class: `<file_stem>_<class_name_lowercase>` (예: `index_index`)
- Method: `<file_stem>_<class_name_lowercase>_<method_name>` (예: `index_index_lookup`)
- Decorator: `decorator_<dec_name>` (전역 — 모든 file에서 같은 데코레이터 공유)
- Rationale: `<source_file_stem>_docstring_<line>`
