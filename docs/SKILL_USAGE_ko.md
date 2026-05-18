# SKILL 사용법

> English: [SKILL_USAGE.md](./SKILL_USAGE.md)

Claude Code에서 pocket-graph SKILL이 어떻게 호출되고 그래프를 어떻게 질의하는지 설명합니다. 설치는 [메인 README](../README.md)를 참고하세요.

## SKILL이 작동하는 질문

번들된 `SKILL.md`는 그래프 traversal이 필요한 질문에 활성화됩니다:

- **관계 질문** — "what calls X", "X와 Y 사이의 경로", "@classmethod 함수들", "class X 멤버"
- **구조 질문** — "두 community를 잇는 노드", "class Index의 hub"
- **변경 추적** — "어제 그래프와 비교"

## SKILL을 쓰지 않는 게 나은 질문

- 자유 텍스트 요약 ("scoring 어떻게 동작하나") → 파일을 직접 읽는 게 낫다.
- 노드 본문 안의 정확한 값 ("이 함수 timeout 몇 초?") → 그래프는 위치만 저장. 본문은 따로 fetch.
- 작은 corpus (10개 미만 파일) → `grep`이 더 빠르다.

## 4-step query workflow

SKILL이 그래프 질문에 답할 때 내재적으로 따르는 흐름:

### Step 1 — IDENTIFY_NODES

질문에 등장한 entity를 노드 ID로 매핑합니다.

```
사용자: "Index.lookup을 호출하는 함수?"
Claude: find_nodes(G, "lookup") → [{"id": "index_index_lookup", ...}]
```

### Step 2 — CHOOSE_TRAVERSAL

| 질문 형태 | 도구 |
|---|---|
| "X가 부르는 것" | `callees_of(node_id)` |
| "X를 부르는 것" | `callers_of(node_id)` |
| "A에서 B까지 경로" | `shortest_path(src, tgt)` |
| "X 주변 1–2 hop" | `get_neighbors(node_id, depth)` |
| "@decorator 사용처" | `filter_by_decorator(decorator)` |
| "T 관계 모두" | `filter_by_relation(relation)` |
| "X가 속한 그룹" | `find_hyperedges_for(node_id)` |
| "전체 그룹 목록" | `list_hyperedges(type)` |
| "두 community 사이 bridge" | `bridging_nodes(he_a, he_b)` |
| "X 그룹의 hub" | `centrality_within(he_id)` |
| "두 시점 그래프 비교" | `evolution_diff(old, new)` |

### Step 3 — EXECUTE

도구를 호출합니다. 보통 1번이면 충분합니다.

### Step 4 — ANSWER_FROM_GRAPH

그래프 결과만으로 답합니다. **본문 fetch는 그래프가 반환한 `source_file:source_location`을 보고 실제 코드가 필요할 때만** 수행합니다.

## 토큰 효율

| 질문 유형 | grep | read-files | graph |
|---|---:|---:|---:|
| 단순 텍스트 매칭 | ~100 | 1000+ | 500–1300 |
| 관계 traversal (callers, path) | 답 못 함 | 8000+ | 300–500 |
| Hyperedge (community 멤버) | 답 못 함 | 12000+ | 300 |

평균적으로 graph는 **read-files 대비 약 88% 절감**, grep과 비슷한 비용으로 **훨씬 넓은 범위의 질문에 답할 수 있다.**

## MCP server

Claude Code나 다른 MCP client에 그래프를 도구로 노출:

```bash
pocket-graph serve ./graph-out/graph.json
```

JSON-RPC 2.0 stdio. 13개 도구 노출:

`find_nodes`, `get_node`, `get_neighbors`, `shortest_path`, `filter_by_relation`, `filter_by_decorator`, `callees_of`, `callers_of`, `find_hyperedges_for`, `list_hyperedges`, `bridging_nodes`, `centrality_within`, `evolution_diff`.

Claude Code MCP 설정 예시:

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

(정확한 설정 키는 Claude Code 버전에 따라 다를 수 있으니 본인 docs 확인.)

## 예시 대화

```
사용자: SearchPipeline.query에서 Index.lookup까지 어떻게 가나?

Claude (SKILL 실행):
  1. find_nodes("SearchPipeline.query") → pipeline_searchpipeline_query
  2. find_nodes("Index.lookup")         → index_index_lookup
  3. shortest_path(...)
  Result: query() → top_k() → score() → lookup()   (length 3)

답: "query()에서 BM25Ranker.top_k() 거쳐 score() 안에서 Index.lookup()을
호출. ranker.py:21에 있는 cross-class 호출."
```
