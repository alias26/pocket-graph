"""Export the graph to various formats: HTML viz, GraphML, Obsidian vault.

graph.html -- self-contained interactive viz (no external CDN, runs offline).
GraphML -- for Gephi/yEd.
Obsidian -- markdown vault with [[wikilinks]] for each node.
"""
from __future__ import annotations
import json
from pathlib import Path
import networkx as nx


# ============================================================
# graph.html -- self-contained interactive viz
# ============================================================
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; background:#0f1115; color:#e6e6e6; }}
  #header {{ position:fixed; top:0; left:0; right:0; padding:12px 16px; background:#1a1d24; border-bottom:1px solid #2a2e36; z-index:10; display:flex; gap:12px; align-items:center; }}
  #header h1 {{ font-size:14px; font-weight:600; margin:0; flex:1; }}
  #header input {{ width:280px; padding:6px 10px; background:#0f1115; border:1px solid #2a2e36; color:#e6e6e6; border-radius:4px; font-size:13px; }}
  #header select {{ padding:6px; background:#0f1115; border:1px solid #2a2e36; color:#e6e6e6; border-radius:4px; font-size:13px; }}
  #stats {{ font-size:12px; color:#888; }}
  #graph {{ position:fixed; top:50px; left:0; right:340px; bottom:0; }}
  #sidebar {{ position:fixed; top:50px; right:0; bottom:0; width:340px; background:#1a1d24; border-left:1px solid #2a2e36; padding:16px; overflow-y:auto; box-sizing:border-box; }}
  #sidebar h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:0.5px; color:#888; margin:0 0 8px 0; }}
  #sidebar h3 {{ font-size:14px; margin:16px 0 6px 0; }}
  #sidebar .label {{ font-size:11px; color:#888; text-transform:uppercase; }}
  #sidebar .field {{ margin:4px 0; font-size:13px; }}
  #sidebar .neighbor {{ display:block; padding:4px 8px; margin:2px 0; background:#0f1115; border-radius:3px; font-size:12px; cursor:pointer; }}
  #sidebar .neighbor:hover {{ background:#2a2e36; }}
  .pill {{ display:inline-block; padding:1px 6px; border-radius:3px; font-size:10px; background:#2a2e36; color:#aaa; margin-right:4px; }}
  .pill.code {{ background:#1e3a5f; color:#9cc8ff; }}
  .pill.paper {{ background:#3a2b5f; color:#c89cff; }}
  .pill.document {{ background:#5f3a1e; color:#ffc89c; }}
  .pill.rationale {{ background:#3a5f1e; color:#9cffc8; }}
  .pill.concept {{ background:#5f1e3a; color:#ff9cc8; }}
  .pill.EXTRACTED {{ background:#1a3a1a; color:#8fcc8f; }}
  .pill.INFERRED {{ background:#3a3a1a; color:#cccc8f; }}
  .pill.AMBIGUOUS {{ background:#3a1a1a; color:#cc8f8f; }}
  svg {{ width:100%; height:100%; cursor:grab; }}
  svg:active {{ cursor:grabbing; }}
  .node circle {{ stroke:#1a1d24; stroke-width:1.5; cursor:pointer; }}
  .node text {{ pointer-events:none; font-size:10px; fill:#ccc; }}
  .node.highlight circle {{ stroke:#fff; stroke-width:2; }}
  .link {{ stroke-opacity:0.4; }}
  .link.highlight {{ stroke-opacity:1; stroke-width:2; }}
</style>
</head>
<body>
<div id="header">
  <h1>{title}</h1>
  <input id="search" placeholder="Search nodes...">
  <select id="filter-type">
    <option value="">all types</option>
    <option value="code">code</option>
    <option value="paper">paper</option>
    <option value="document">document</option>
    <option value="rationale">rationale</option>
    <option value="concept">concept</option>
  </select>
  <select id="filter-hyperedge">
    <option value="">all hyperedges</option>
  </select>
  <span id="stats"></span>
</div>
<div id="graph"><svg></svg></div>
<div id="sidebar"><h2>Click a node</h2></div>

<script>
const DATA = {graph_json};

const TYPE_COLORS = {{
  code: "#3b82f6", paper: "#a855f7", document: "#f97316",
  rationale: "#22c55e", concept: "#ec4899",
}};

// d3-style simulation, but vanilla -- stable force layout with cooling
function simulation(nodes, links, width, height) {{
  const N = nodes.length;
  // Initial layout: spread nodes on a jittered grid filling the viewport
  // with strong jitter so they don't form a visible grid pattern.
  const cols = Math.max(1, Math.ceil(Math.sqrt(N)));
  const rows = Math.max(1, Math.ceil(N / cols));
  const cellW = width / (cols + 1);
  const cellH = height / (rows + 1);
  const xs = new Array(N);
  const ys = new Array(N);
  for (let i = 0; i < N; i++) {{
    const col = i % cols, row = Math.floor(i / cols);
    xs[i] = cellW * (col + 1) + (Math.random() - 0.5) * cellW * 0.6;
    ys[i] = cellH * (row + 1) + (Math.random() - 0.5) * cellH * 0.6;
  }}
  const vxs = new Array(N).fill(0);
  const vys = new Array(N).fill(0);
  const idx = new Map(nodes.map((n,i)=>[n.id,i]));
  const adj = new Array(N).fill(0).map(()=>[]);
  // Weighted degree: sum of edge weights touching the node. Used for node sizing
  // so that hot-path callers (high call count) appear larger than rarely-called ones.
  const weightedDeg = new Array(N).fill(0);
  for (const l of links) {{
    const a = idx.get(l.source), b = idx.get(l.target);
    if (a!=null && b!=null) {{
      adj[a].push(b); adj[b].push(a);
      const w = l.weight || 1;
      weightedDeg[a] += w;
      weightedDeg[b] += w;
    }}
  }}

  // Tunables -- tested for ~50–500 node graphs.
  const REPULSION_K = 2200;     // strength of node–node repulsion
  const REPULSION_MIN_D = 8;    // clamp closest distance to avoid divergence
  const REPULSION_MAX_F = 6;    // cap force per pair (raised for better spread)
  const SPRING_REST = 60;       // ideal link length in pixels
  const SPRING_K = 0.02;        // stiffness
  const CENTER_K = 0.005;       // pull toward center (weakened — let nodes spread)
  const DAMPING = 0.7;          // velocity multiplier per frame
  const VELOCITY_CAP = 8;       // max px movement per frame
  let alpha = 1.0;              // cooling factor -- multiplies all forces
  const ALPHA_DECAY = 0.003;    // frame-by-frame decay (slowed for fuller spread)
  const ALPHA_MIN = 0.02;       // freeze threshold

  // Compute connected components. Repulsion (and attraction) only acts
  // within the same component, so dragging a node in one cluster does not
  // shake nodes in unrelated clusters or isolated singletons.
  const component = new Array(N).fill(-1);
  let nextComp = 0;
  for (let i = 0; i < N; i++) {{
    if (component[i] !== -1) continue;
    const stack = [i];
    component[i] = nextComp;
    while (stack.length > 0) {{
      const v = stack.pop();
      for (const u of adj[v]) {{
        if (component[u] === -1) {{
          component[u] = nextComp;
          stack.push(u);
        }}
      }}
    }}
    nextComp++;
  }}

  // Fixed nodes: their positions are set externally (e.g. by drag) and not
  // affected by forces. We still allow the simulation to push other nodes
  // toward/away from them via attraction/repulsion.
  const fixed = new Set();
  // Isolated nodes (zero edges) are pinned by default — they have no
  // attraction toward anything, so without pinning they get pushed around
  // by every other node's repulsion. Pinning them lets the user see them
  // sitting calmly at their initial position. Drag still works (drag
  // handler can still reposition them; release leaves them pinned).
  for (let i = 0; i < N; i++) {{
    if (adj[i].length === 0) fixed.add(i);
  }}
  function reheat(value) {{
    if (alpha < value) alpha = value;
  }}

  function step() {{
    if (alpha < ALPHA_MIN && fixed.size === 0) return;  // converged -- stop computing

    // Repulsion: O(n²) within each component, with distance-clamping.
    // Cross-component pairs are skipped — dragging a node in one cluster
    // shouldn't shake nodes in unrelated clusters.
    for (let i = 0; i < N; i++) {{
      for (let j = i + 1; j < N; j++) {{
        if (component[i] !== component[j]) continue;
        let dx = xs[j] - xs[i], dy = ys[j] - ys[i];
        let d2 = dx * dx + dy * dy;
        if (d2 < REPULSION_MIN_D * REPULSION_MIN_D) {{
          // Two nodes essentially overlap -- give them a deterministic shove apart
          d2 = REPULSION_MIN_D * REPULSION_MIN_D;
          // Add tiny jitter so symmetric overlaps still escape
          if (dx === 0 && dy === 0) {{ dx = (Math.random() - 0.5); dy = (Math.random() - 0.5); }}
        }}
        const d = Math.sqrt(d2);
        let f = (REPULSION_K * alpha) / d2;
        if (f > REPULSION_MAX_F) f = REPULSION_MAX_F;
        const fx = f * dx / d, fy = f * dy / d;
        vxs[i] -= fx; vys[i] -= fy;
        vxs[j] += fx; vys[j] += fy;
      }}
    }}

    // Attraction: spring law toward rest length. Stable for any distance.
    for (const l of links) {{
      const a = idx.get(l.source), b = idx.get(l.target);
      if (a == null || b == null) continue;
      const dx = xs[b] - xs[a], dy = ys[b] - ys[a];
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const stretch = d - SPRING_REST;
      const force = stretch * SPRING_K * alpha;
      const fx = force * dx / d, fy = force * dy / d;
      vxs[a] += fx; vys[a] += fy;
      vxs[b] -= fx; vys[b] -= fy;
    }}

    // Center gravity + damping + velocity cap + integrate + boundary clamp
    for (let i = 0; i < N; i++) {{
      vxs[i] += (width / 2 - xs[i]) * CENTER_K * alpha;
      vys[i] += (height / 2 - ys[i]) * CENTER_K * alpha;
      vxs[i] *= DAMPING;
      vys[i] *= DAMPING;
      // Velocity cap
      if (vxs[i] >  VELOCITY_CAP) vxs[i] =  VELOCITY_CAP;
      if (vxs[i] < -VELOCITY_CAP) vxs[i] = -VELOCITY_CAP;
      if (vys[i] >  VELOCITY_CAP) vys[i] =  VELOCITY_CAP;
      if (vys[i] < -VELOCITY_CAP) vys[i] = -VELOCITY_CAP;
      xs[i] += vxs[i]; ys[i] += vys[i];
      // Boundary clamp -- keep nodes inside viewport with margin
      const M = 20;
      if (xs[i] < M)            {{ xs[i] = M;            vxs[i] = 0; }}
      if (xs[i] > width - M)    {{ xs[i] = width - M;    vxs[i] = 0; }}
      if (ys[i] < M)            {{ ys[i] = M;            vys[i] = 0; }}
      if (ys[i] > height - M)   {{ ys[i] = height - M;   vys[i] = 0; }}
    }}

    // Fixed nodes: keep their externally-set position, ignore accumulated forces.
    for (const i of fixed) {{
      vxs[i] = 0; vys[i] = 0;
    }}

    alpha -= ALPHA_DECAY;
  }}

  // God-node threshold: 95th percentile of weighted degree, with a floor of 5
  // to avoid flagging everything in tiny graphs. Used by node renderer.
  const sortedWdeg = [...weightedDeg].sort((a, b) => a - b);
  const p95 = sortedWdeg[Math.floor(sortedWdeg.length * 0.95)] || 0;
  const godThreshold = Math.max(5, p95);

  return {{ xs, ys, vxs, vys, step, idx, adj, weightedDeg, godThreshold, fixed, reheat }};
}}

function init() {{
  const svg = document.querySelector("svg");
  const W = svg.clientWidth, H = svg.clientHeight;
  const nodes = DATA.nodes;
  const links = DATA.links || DATA.edges || [];

  const sim = simulation(nodes, links, W, H);

  // Pre-warm the simulation so the very first paint is already settled.
  // 800 steps with the slowed cooling gives roughly the same convergence
  // as the old 500/0.005 combo but produces a wider spread.
  for (let i = 0; i < 800; i++) sim.step();

  // Render
  const linkGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  const nodeGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  svg.appendChild(linkGroup);
  svg.appendChild(nodeGroup);

  const linkEls = links.map(l => {{
    const a = sim.idx.get(l.source), b = sim.idx.get(l.target);
    if (a==null || b==null) return null;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("class", `link conf-${{l.confidence||""}}`);
    line.setAttribute("stroke", l.confidence==="AMBIGUOUS"?"#cc8f8f":(l.confidence==="INFERRED"?"#cccc8f":"#666"));
    line.setAttribute("stroke-dasharray", l.confidence==="INFERRED"?"3,3":(l.confidence==="AMBIGUOUS"?"1,4":""));
    line.dataset.relation = l.relation || "";
    line.dataset.source = l.source;
    line.dataset.target = l.target;
    linkGroup.appendChild(line);
    return line;
  }}).filter(Boolean);

  const nodeEls = nodes.map((n,i) => {{
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "node");
    g.dataset.id = n.id;
    g.dataset.label = (n.label||"").toLowerCase();
    g.dataset.type = n.file_type || "";
    g.dataset.community = (n.community != null) ? n.community : "";
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    // Node size is driven by weighted degree (call count + import count + ...).
    // Range 4-26 px gives ~6x ratio between leaves and god nodes, far more
    // legible than the old 4-12 (2.3x) which clustered all hubs near the cap.
    // sqrt scaling keeps mid-range readable while highlighting outliers.
    const wdeg = sim.weightedDeg[i] || sim.adj[i].length;
    const r = Math.max(4, Math.min(26, 4 + 3.0 * Math.sqrt(wdeg)));
    circle.setAttribute("r", r);
    circle.setAttribute("fill", TYPE_COLORS[n.file_type]||"#888");
    // God nodes (top 5% by weighted degree) get an extra-thick outer stroke
    // to catch the eye in dense graphs.
    const isGodNode = wdeg >= sim.godThreshold;
    // Community stroke: a colored ring whose hue maps to community id.
    if (n.community != null) {{
      const hue = (n.community * 47) % 360;  // golden-ratio-ish spread
      circle.setAttribute("stroke", `hsl(${{hue}}, 60%, 55%)`);
      circle.setAttribute("stroke-width", isGodNode ? "3.5" : "2");
    }} else if (isGodNode) {{
      circle.setAttribute("stroke", "#fff");
      circle.setAttribute("stroke-width", "2");
    }}
    g.appendChild(circle);
    // Hover tooltip: full label always available via native SVG <title>
    {{
      const titleEl = document.createElementNS("http://www.w3.org/2000/svg", "title");
      titleEl.textContent = n.label || n.id;
      g.appendChild(titleEl);
    }}
    // Visible label: shorter cap (24 chars) to keep the canvas readable.
    // Long labels get truncated; the full label is in the hover tooltip and
    // the sidebar when the node is clicked.
    {{
      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      const fullLabel = n.label || n.id;
      text.textContent = fullLabel.length > 24
                         ? fullLabel.substring(0, 22) + "…"
                         : fullLabel;
      text.setAttribute("dx", r+3);
      text.setAttribute("dy", 4);
      if (isGodNode) {{
        text.setAttribute("font-weight", "600");
        text.setAttribute("fill", "#fff");
      }}
      g.appendChild(text);
    }}
    nodeGroup.appendChild(g);
    // Mouse interaction: distinguish click (small movement) from drag (>5px).
    // Drag uses mousedown on the node + mousemove/mouseup on window so the
    // cursor doesn't lose the node when it moves fast.
    g.addEventListener("mousedown", e => {{
      e.stopPropagation();  // don't trigger SVG pan
      const startPt = clientToSvg(e.clientX, e.clientY);
      const offset = {{ x: startPt.x - sim.xs[i], y: startPt.y - sim.ys[i] }};
      const startClient = {{ x: e.clientX, y: e.clientY }};
      let moved = false;
      sim.fixed.add(i);
      sim.reheat(0.4);  // wake the simulation so other nodes follow
      g.style.cursor = "grabbing";

      function onMove(ev) {{
        const dxClient = ev.clientX - startClient.x;
        const dyClient = ev.clientY - startClient.y;
        if (!moved && (dxClient*dxClient + dyClient*dyClient) > 25) moved = true;
        if (moved) {{
          const pt = clientToSvg(ev.clientX, ev.clientY);
          sim.xs[i] = pt.x - offset.x;
          sim.ys[i] = pt.y - offset.y;
          sim.reheat(0.3);
        }}
      }}
      function onUp() {{
        sim.fixed.delete(i);
        g.style.cursor = "";
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        if (!moved) showDetail(n);  // treat as click
      }}
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    }});
    g.style.cursor = "grab";
    return g;
  }});

  function refreshPositions() {{
    for (let i=0; i<nodes.length; i++) {{
      nodeEls[i].setAttribute("transform", `translate(${{sim.xs[i]}},${{sim.ys[i]}})`);
    }}
    for (let i=0; i<links.length; i++) {{
      if (!linkEls[i]) continue;
      const a = sim.idx.get(links[i].source), b = sim.idx.get(links[i].target);
      if (a==null || b==null) continue;
      linkEls[i].setAttribute("x1", sim.xs[a]);
      linkEls[i].setAttribute("y1", sim.ys[a]);
      linkEls[i].setAttribute("x2", sim.xs[b]);
      linkEls[i].setAttribute("y2", sim.ys[b]);
    }}
  }}
  refreshPositions();

  // Animation loop runs forever, but sim.step() early-returns once the
  // simulation has cooled (alpha < ALPHA_MIN and no fixed nodes). That keeps
  // CPU near-zero in the steady state while still letting drag interactions
  // wake the layout via reheat().
  function loop() {{
    sim.step();
    refreshPositions();
    requestAnimationFrame(loop);
  }}
  loop();

  // Pan / zoom
  let panX = 0, panY = 0, zoom = 1;
  let dragging = false, dragStart = null;

  // Convert client (mouse) coordinates to SVG-internal coordinates that
  // match xs/ys. Required for node dragging because the canvas may be panned
  // and zoomed at the same time.
  function clientToSvg(cx, cy) {{
    const rect = svg.getBoundingClientRect();
    return {{
      x: (cx - rect.left - panX) / zoom,
      y: (cy - rect.top - panY) / zoom,
    }};
  }}
  svg.addEventListener("mousedown", e => {{
    if (e.target.tagName === "svg") {{
      dragging = true;
      dragStart = {{ x: e.clientX - panX, y: e.clientY - panY }};
    }}
  }});
  svg.addEventListener("mousemove", e => {{
    if (dragging) {{
      panX = e.clientX - dragStart.x;
      panY = e.clientY - dragStart.y;
      apply();
    }}
  }});
  window.addEventListener("mouseup", () => dragging = false);
  svg.addEventListener("wheel", e => {{
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    zoom *= factor;
    zoom = Math.max(0.1, Math.min(zoom, 5));
    apply();
  }});
  function apply() {{
    [linkGroup, nodeGroup].forEach(g => g.setAttribute("transform",
      `translate(${{panX}},${{panY}}) scale(${{zoom}})`));
  }}

  // Search
  document.getElementById("search").addEventListener("input", e => {{
    const q = e.target.value.toLowerCase();
    nodeEls.forEach(g => {{
      const match = !q || g.dataset.label.includes(q) || g.dataset.id.toLowerCase().includes(q);
      g.style.opacity = match ? 1 : 0.15;
    }});
  }});
  // Populate type dropdown dynamically -- only show types present in the graph,
  // with counts so users see at a glance what's available.
  const typeCounts = {{}};
  nodes.forEach(n => {{
    const t = n.file_type || '?';
    typeCounts[t] = (typeCounts[t] || 0) + 1;
  }});
  const typeSelect = document.getElementById("filter-type");
  while (typeSelect.options.length > 1) typeSelect.remove(1);
  Object.keys(typeCounts).sort().forEach(t => {{
    const opt = document.createElement("option");
    opt.value = t;
    opt.textContent = `${{t}} (${{typeCounts[t]}})`;
    typeSelect.appendChild(opt);
  }});

  // Populate hyperedge dropdown
  const hyperedges = DATA.hyperedges || [];
  const heSelect = document.getElementById("filter-hyperedge");
  hyperedges.forEach(he => {{
    const opt = document.createElement("option");
    opt.value = he.id;
    opt.textContent = `[${{he.type}}] ${{(he.label||he.id).substring(0,50)}}`;
    heSelect.appendChild(opt);
  }});

  function applyFilters() {{
    const typeFilter = typeSelect.value;
    const heFilter = heSelect.value;
    const heMembers = heFilter
      ? new Set((hyperedges.find(h => h.id === heFilter) || {{}}).members || [])
      : null;

    // Compute which nodes are visible
    const visibleNodes = new Set();
    nodes.forEach((n, i) => {{
      const passType = !typeFilter || (n.file_type || '?') === typeFilter;
      const passHe = !heMembers || heMembers.has(n.id);
      if (passType && passHe) visibleNodes.add(n.id);
    }});

    // Apply to nodes
    nodeEls.forEach((g, i) => {{
      const visible = visibleNodes.has(nodes[i].id);
      g.style.display = visible ? "" : "none";
      // hyperedge dimming: when only HE filter active, dim non-members instead
      // of hiding (gives a "spotlight" effect that preserves graph structure).
      if (heFilter && !typeFilter) {{
        g.style.display = "";
        g.style.opacity = visible ? 1 : 0.1;
      }} else {{
        g.style.opacity = 1;
      }}
    }});

    // Apply to links: hide if either endpoint is hidden, dim under HE-only filter
    links.forEach((l, i) => {{
      const el = linkEls[i];
      if (!el) return;
      const aVis = visibleNodes.has(l.source);
      const bVis = visibleNodes.has(l.target);
      if (heFilter && !typeFilter) {{
        // HE-only mode: keep all edges, dim those not connecting two members
        el.style.display = "";
        el.style.opacity = (aVis && bVis) ? 0.6 : 0.05;
      }} else {{
        el.style.display = (aVis && bVis) ? "" : "none";
        el.style.opacity = "";
      }}
    }});
  }}

  typeSelect.addEventListener("change", applyFilters);
  heSelect.addEventListener("change", applyFilters);

  // Stats
  document.getElementById("stats").textContent =
    `${{nodes.length}} nodes · ${{links.length}} edges`;

  function showDetail(n) {{
    const sb = document.getElementById("sidebar");
    const neighbors = links
      .filter(l => l.source === n.id || l.target === n.id)
      .map(l => ({{
        otherId: l.source === n.id ? l.target : l.source,
        relation: l.relation,
        confidence: l.confidence,
        direction: l.source === n.id ? "->" : "<-",
      }}));
    let html = `<h2>${{(n.label||n.id).substring(0,80)}}</h2>`;
    html += `<div class="field"><span class="pill ${{n.file_type||''}}">${{n.file_type||'?'}}</span></div>`;
    if (n.source_file) html += `<div class="field"><span class="label">file</span> <code>${{n.source_file}}</code></div>`;
    if (n.source_location) html += `<div class="field"><span class="label">location</span> ${{n.source_location}}</div>`;
    if (n.body) html += `<div class="field"><span class="label">body</span><br><pre style="white-space:pre-wrap;font-size:11px;color:#aaa;">${{n.body.substring(0,500).replace(/[<>&]/g,c=>({{'<':'&lt;','>':'&gt;','&':'&amp;'}}[c]))}}</pre></div>`;
    html += `<h3>Neighbors (${{neighbors.length}})</h3>`;
    for (const nb of neighbors.slice(0, 30)) {{
      const other = nodes.find(nn => nn.id === nb.otherId);
      const lbl = (other?.label || nb.otherId).substring(0, 50);
      html += `<div class="neighbor" data-id="${{nb.otherId}}">
        <span class="pill ${{nb.confidence||''}}">${{nb.confidence||''}}</span>
        ${{nb.direction}} <code>${{nb.relation||''}}</code> ${{lbl}}
      </div>`;
    }}
    sb.innerHTML = html;
    sb.querySelectorAll(".neighbor").forEach(el => {{
      el.addEventListener("click", () => {{
        const target = nodes.find(nn => nn.id === el.dataset.id);
        if (target) showDetail(target);
      }});
    }});
  }}
}}

init();
</script>
</body>
</html>
"""


def export_html(G: nx.Graph, out_path: Path, title: str = "pocket-graph") -> None:
    """Write a self-contained interactive HTML viewer."""
    from .build import to_node_link
    data = to_node_link(G)
    html = _HTML_TEMPLATE.format(
        title=title,
        graph_json=json.dumps(data, ensure_ascii=False),
    )
    Path(out_path).write_text(html, encoding="utf-8")


# ============================================================
# GraphML -- for Gephi/yEd
# ============================================================
def export_graphml(G: nx.Graph, out_path: Path) -> None:
    """Export to GraphML for Gephi/yEd.

    GraphML's data type system supports only int/float/bool/string. Any list
    or dict attribute is JSON-encoded into a string, and None values are
    dropped -- otherwise NetworkX raises TypeError.
    """
    H = nx.DiGraph() if G.is_directed() else nx.Graph()

    def _coerce(v):
        if v is None:
            return None  # caller should drop
        if isinstance(v, (str, int, float, bool)):
            return v
        # list / dict / tuple / etc. -> JSON string
        try:
            return json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(v)

    def _scrub(attrs: dict) -> dict:
        out = {}
        for k, v in attrs.items():
            cv = _coerce(v)
            if cv is None:
                continue
            out[str(k)] = cv
        return out

    for nid, attrs in G.nodes(data=True):
        H.add_node(str(nid), **_scrub(attrs))
    for u, v, attrs in G.edges(data=True):
        H.add_edge(str(u), str(v), **_scrub(attrs))

    nx.write_graphml(H, out_path)


# ============================================================
# Obsidian vault -- one .md file per node with [[wikilinks]]
# ============================================================
def export_obsidian(G: nx.Graph, out_dir: Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for nid, attrs in G.nodes(data=True):
        label = attrs.get("label", nid)
        ftype = attrs.get("file_type", "")
        safe_name = nid.replace("/", "_")[:120]
        body = [f"# {label}\n",
                f"**Type:** {ftype}",
                f"**Source:** `{attrs.get('source_file', '')}`",
                f"**Location:** {attrs.get('source_location', '')}\n"]
        if "body" in attrs:
            body.append(f"## Body\n```\n{attrs['body'][:2000]}\n```\n")
        # Outgoing edges
        out_edges = list(G.out_edges(nid, data=True)) if G.is_directed() else []
        in_edges = list(G.in_edges(nid, data=True)) if G.is_directed() else []
        if out_edges:
            body.append("## Outgoing\n")
            for _, t, d in out_edges:
                t_label = G.nodes[t].get("label", t)
                t_safe = t.replace("/", "_")[:120]
                body.append(f"- `{d.get('relation', '')}` [[{t_safe}|{t_label}]] _({d.get('confidence', '')})_")
        if in_edges:
            body.append("\n## Incoming\n")
            for s, _, d in in_edges:
                s_label = G.nodes[s].get("label", s)
                s_safe = s.replace("/", "_")[:120]
                body.append(f"- [[{s_safe}|{s_label}]] `{d.get('relation', '')}` _({d.get('confidence', '')})_")
        (out_dir / f"{safe_name}.md").write_text("\n".join(body, encoding="utf-8"))


# ============================================================
# Neo4j Cypher -- for graph database import
# ============================================================
def export_neo4j_cypher(G: nx.Graph, out_path: Path) -> None:
    """Generate a Cypher script that recreates the graph in Neo4j.

    Usage in Neo4j Browser or cypher-shell:
        :source cypher.txt
    
    Or via cypher-shell:
        cat cypher.txt | cypher-shell -u neo4j -p <password>
    
    Edge relations become Neo4j relationship types (UPPERCASE_SNAKE_CASE).
    Node `file_type` becomes the Neo4j label.
    All other node attrs become properties.
    """
    out_path = Path(out_path)
    lines: list[str] = [
        "// pocket-graph -> Neo4j Cypher",
        "// Generated by pocket_graph.export.export_neo4j_cypher",
        "",
        "// 1. Reset (optional -- uncomment to clear before import)",
        "// MATCH (n) DETACH DELETE n;",
        "",
        "// 2. Create unique constraint on node id",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE;",
        "",
        "// 3. Nodes",
    ]

    # Map file_types to Neo4j-friendly labels
    label_map = {
        "code": "Code",
        "document": "Document",
        "paper": "Paper",
        "image": "Image",
        "rationale": "Rationale",
        "concept": "Concept",
    }

    def _esc(v):
        """Escape a value for Cypher."""
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        # string -- escape backslashes and quotes
        s = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        return f"'{s}'"

    for nid, attrs in G.nodes(data=True):
        ftype = attrs.get("file_type", "concept")
        neo_label = label_map.get(ftype, "Node")
        # Build properties dict
        props = {"id": nid}
        for k, v in attrs.items():
            if k == "id":
                continue
            # Neo4j doesn't support nested objects in CREATE; flatten or skip
            if isinstance(v, (dict, list)):
                continue
            if v is None:
                continue
            props[k] = v
        prop_str = ", ".join(f"{k}: {_esc(v)}" for k, v in props.items())
        lines.append(f"CREATE (:Node:{neo_label} {{{prop_str}}});")

    lines.append("")
    lines.append("// 4. Relationships")

    for u, v, data in G.edges(data=True):
        rel = data.get("relation", "RELATED").upper()
        # Cypher relationship types: must be valid identifier
        rel = "".join(c if c.isalnum() else "_" for c in rel).strip("_") or "RELATED"
        confidence = data.get("confidence", "EXTRACTED")
        rel_props = {"confidence": confidence}
        for k, val in data.items():
            if k in ("relation", "confidence", "source_file", "_src", "_tgt"):
                continue
            if isinstance(val, (dict, list)) or val is None:
                continue
            rel_props[k] = val
        if data.get("source_file"):
            rel_props["source_file"] = data["source_file"]

        prop_str = ", ".join(f"{k}: {_esc(val)}" for k, val in rel_props.items())
        lines.append(
            f"MATCH (a:Node {{id: {_esc(u)}}}), (b:Node {{id: {_esc(v)}}}) "
            f"CREATE (a)-[:{rel} {{{prop_str}}}]->(b);"
        )

    # Hyperedges: Neo4j has no n-ary edges natively, so represent each hyperedge
    # as a hub node + MEMBER_OF edges from each member back to the hub.
    hyperedges = G.graph.get("hyperedges", [])
    if hyperedges:
        lines.append("")
        lines.append("// 5. Hyperedges (modeled as hub nodes + MEMBER_OF edges)")
        for he in hyperedges:
            hid = he.get("id", "")
            label = he.get("label", "")
            htype = he.get("type", "hyperedge")
            members = he.get("members", [])
            props = {
                "id": hid, "label": label, "type": htype, "size": len(members),
            }
            prop_str = ", ".join(f"{k}: {_esc(v)}" for k, v in props.items())
            lines.append(f"CREATE (:HyperEdge {{{prop_str}}});")
            for m in members:
                lines.append(
                    f"MATCH (h:HyperEdge {{id: {_esc(hid)}}}), "
                    f"(n:Node {{id: {_esc(m)}}}) "
                    f"CREATE (n)-[:MEMBER_OF]->(h);"
                )

    lines.append("")
    lines.append("// 6. Useful queries to run after import:")
    lines.append("// MATCH (n:Node) RETURN n LIMIT 25;")
    lines.append("// MATCH (a)-[r:CALLS]->(b) RETURN a, r, b LIMIT 50;")
    lines.append("// MATCH p = shortestPath((a:Node {label:'run()'})-[*]-(b:Node {label:'lookup()'})) RETURN p;")
    lines.append("// MATCH (n) WHERE n.label STARTS WITH '@' RETURN n;  // decorators")
    lines.append("// MATCH (h:HyperEdge {type:'class_group'})<-[:MEMBER_OF]-(n) RETURN h, n;  // class groupings")
    lines.append("")

    out_path.write_text("\n".join(lines, encoding="utf-8"), encoding="utf-8")


__all__ = ["export_html", "export_graphml", "export_obsidian", "export_neo4j_cypher"]
