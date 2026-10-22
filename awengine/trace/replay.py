"""Self-contained HTML replay.

The file embeds the event stream and a tiny vanilla-JS player: play/pause,
step forward/back, timeline, keyboard shortcuts.  No CDN, no network, no build
step - open it from the runs folder and it works.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from ..contracts import RunState
from ..spec import WorkflowSpec


def _graph_nodes(spec: WorkflowSpec) -> list[dict[str, Any]]:
    layers: dict[str, int] = {}
    order = [spec.entry] + [node.name for node in spec.nodes if node.name != spec.entry]
    for name in order:
        best = 0
        for edge in spec.in_edges(name):
            if edge.src in layers:
                best = max(best, layers[edge.src] + 1)
        layers[name] = best
    by_layer: dict[int, list[str]] = {}
    for name in order:
        by_layer.setdefault(layers[name], []).append(name)
    nodes: list[dict[str, Any]] = []
    width = 980
    for layer, names in by_layer.items():
        for index, name in enumerate(names):
            x = 40 + (index + 0.5) * (width / max(1, len(names)))
            y = 30 + layer * 110
            node = spec.node(name)
            nodes.append(
                {
                    "id": name,
                    "x": round(x, 1),
                    "y": y,
                    "type": node.type if node else "?",
                }
            )
    return nodes


def _graph_svg(spec: WorkflowSpec) -> str:
    nodes = _graph_nodes(spec)
    positions = {node["id"]: node for node in nodes}
    height = max((node["y"] for node in nodes), default=100) + 80
    parts = [
        f'<svg id="graph" viewBox="0 0 1000 {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="workflow graph">',
        "<defs><marker id='arrow' markerWidth='10' markerHeight='8' refX='9' refY='4' orient='auto'>"
        "<path d='M0,0 L10,4 L0,8 z' fill='#8aa0b8'/></marker></defs>",
    ]
    for edge in spec.edges:
        src = positions.get(edge.src)
        if not src:
            continue
        if edge.dst == "END":
            x1, y1 = src["x"], src["y"] + 26
            parts.append(
                f"<line x1='{x1}' y1='{y1}' x2='{x1}' y2='{y1 + 34}' stroke='#8aa0b8' stroke-width='1.5' marker-end='url(#arrow)'/>"
            )
            parts.append(
                f"<text x='{x1 + 8}' y='{y1 + 30}' font-size='12' fill='#5b7188'>END</text>"
            )
            continue
        dst = positions.get(edge.dst)
        if not dst:
            continue
        parts.append(
            f"<line x1='{src['x']}' y1='{src['y'] + 26}' x2='{dst['x']}' y2='{dst['y'] - 26}' "
            "stroke='#8aa0b8' stroke-width='1.5' marker-end='url(#arrow)'/>"
        )
        if edge.when:
            mx = (src["x"] + dst["x"]) / 2
            my = (src["y"] + dst["y"]) / 2
            label = html.escape(edge.when[:42])
            parts.append(
                f"<text x='{mx}' y='{my}' font-size='11' fill='#5b7188' text-anchor='middle'>{label}</text>"
            )
    for node in nodes:
        color = {"llm_agent": "#e8f0fe", "process": "#eef7ee"}.get(node["type"], "#fdecea")
        stroke = {"llm_agent": "#4a6fa5", "process": "#2e7d32"}.get(node["type"], "#b3261e")
        parts.append(
            f"<g class='gnode' data-node='{html.escape(node['id'])}'>"
            f"<rect x='{node['x'] - 78}' y='{node['y'] - 26}' width='156' height='52' rx='10' "
            f"fill='{color}' stroke='{stroke}' stroke-width='1.6'/>"
            f"<text x='{node['x']}' y='{node['y'] - 2}' text-anchor='middle' font-size='13' font-weight='600'>{html.escape(node['id'])}</text>"
            f"<text x='{node['x']}' y='{node['y'] + 15}' text-anchor='middle' font-size='10' fill='#5b7188'>{node['type']}</text>"
            "</g>"
        )
    if spec.verify:
        parts.append(
            f"<g><rect x='820' y='{height - 60}' width='160' height='40' rx='8' fill='#fdecea' stroke='#b3261e'/>"
            f"<text x='900' y='{height - 35}' text-anchor='middle' font-size='11'>verify L{spec.verify.level}</text></g>"
        )
    parts.append("</svg>")
    return "".join(parts)


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AWEngine replay - {run_id}</title>
<style>
:root {{ --bg:#f7f9fc; --panel:#ffffff; --ink:#10243f; --muted:#5b7188; --line:#dbe4ef; --accent:#2f6fed; --ok:#2e7d32; --bad:#b3261e; --warn:#a15c00; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0d1723; --panel:#142233; --ink:#e6eef8; --muted:#9db0c6; --line:#24384f; --accent:#7aa7ff; --ok:#7bc47f; --bad:#ff8a80; --warn:#ffc46b; }}
  svg .gnode rect {{ opacity:.9; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:14px/1.5 "Segoe UI", system-ui, sans-serif; background:var(--bg); color:var(--ink); }}
header {{ padding:18px 22px; border-bottom:1px solid var(--line); display:flex; gap:18px; align-items:baseline; flex-wrap:wrap; }}
header h1 {{ font-size:17px; margin:0; }}
header .meta {{ color:var(--muted); font-size:12px; }}
main {{ display:grid; grid-template-columns: minmax(0,1.15fr) minmax(0,1fr); gap:18px; padding:18px 22px 40px; }}
@media (max-width: 900px) {{ main {{ grid-template-columns:1fr; }} }}
section {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; }}
h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:0 0 10px; }}
#graph {{ width:100%; height:auto; }}
.gnode rect {{ transition:stroke-width .15s, filter .15s; }}
.gnode.active rect {{ stroke-width:4; filter:drop-shadow(0 0 6px rgba(47,111,237,.55)); }}
#controls {{ display:flex; gap:8px; align-items:center; margin-bottom:10px; flex-wrap:wrap; }}
button {{ background:var(--accent); color:#fff; border:0; border-radius:8px; padding:7px 12px; cursor:pointer; font:inherit; }}
button.secondary {{ background:transparent; color:var(--ink); border:1px solid var(--line); }}
button:disabled {{ opacity:.45; cursor:default; }}
input[type=range] {{ width:100%; accent-color:var(--accent); }}
#eventlist {{ max-height:470px; overflow:auto; border:1px solid var(--line); border-radius:8px; }}
.event {{ padding:7px 9px; border-bottom:1px solid var(--line); cursor:pointer; display:grid; grid-template-columns:56px 1fr; gap:8px; }}
.event:hover {{ background:rgba(47,111,237,.06); }}
.event.selected {{ background:rgba(47,111,237,.14); }}
.event .seq {{ color:var(--muted); font-variant-numeric:tabular-nums; font-size:12px; }}
.event .type {{ font-weight:600; }}
.event .node {{ color:var(--muted); font-size:12px; }}
pre {{ margin:0; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px; overflow:auto; max-height:200px; font-size:12px; }}
.kv {{ display:grid; grid-template-columns: 90px 1fr; gap:4px 10px; font-size:13px; }}
.kv dt {{ color:var(--muted); }}
.kv dd {{ margin:0; }}
.badge {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; border:1px solid var(--line); }}
.badge.ok {{ color:var(--ok); border-color:var(--ok); }}
.badge.bad {{ color:var(--bad); border-color:var(--bad); }}
#scrub {{ display:flex; align-items:center; gap:10px; margin:10px 0; }}
#clock {{ color:var(--muted); font-variant-numeric:tabular-nums; font-size:12px; min-width:56px; text-align:right; }}
kbd {{ border:1px solid var(--line); border-radius:4px; padding:0 4px; font-size:11px; }}
</style>
</head>
<body>
<header>
  <h1>AWEngine replay</h1>
  <span class="meta">run <code>{run_id}</code> - workflow <code>{workflow}</code></span>
  <span class="meta">terminal <span id="terminal" class="badge">{terminal}</span></span>
  <span class="meta">{event_count} events</span>
</header>
<main>
  <div>
    <section style="margin-bottom:18px">
      <h2>Workflow</h2>
      {graph_svg}
    </section>
    <section>
      <h2>Timeline</h2>
      <div id="controls">
        <button id="play">Play</button>
        <button id="prev" class="secondary">Prev</button>
        <button id="next" class="secondary">Next</button>
        <span id="position" class="meta"></span>
      </div>
      <div id="scrub"><input id="slider" type="range" min="0" max="{max_index}" value="0"><span id="clock">0.0s</span></div>
      <div id="eventlist"></div>
    </section>
  </div>
  <div>
    <section style="margin-bottom:18px">
      <h2>Event detail</h2>
      <dl class="kv" id="detail"></dl>
      <h2 style="margin-top:14px">Raw</h2>
      <pre id="raw"></pre>
    </section>
    <section>
      <h2>Run state at this point</h2>
      <dl class="kv" id="stateview"></dl>
    </section>
  </div>
</main>
<script id="payload" type="application/json">{payload}</script>
<script>
(() => {{
  const data = JSON.parse(document.getElementById('payload').textContent);
  const events = data.events;
  const slider = document.getElementById('slider');
  const list = document.getElementById('eventlist');
  let index = 0, timer = null;
  const esc = (s) => String(s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
  const describe = (e) => {{
    const d = e.data || {{}};
    switch (e.type) {{
      case 'run_start': return `workflow ${{d.workflow}} (${{d.spec_hash?.slice(0,8)}}) entry=${{d.entry}}`;
      case 'node_start': return `${{e.node}} started (round ${{e.round}})`;
      case 'node_end': return `${{e.node}} -> ${{d.status}} (${{d.duration_ms}}ms, ${{d.tokens}} tok)`;
      case 'llm_call': return `${{e.node}} -> ${{d.model}} step ${{d.step}} (+${{d.completion_tokens}} tok, ¥${{d.cost}})`;
      case 'tool_call': return `${{e.node}} calls ${{d.tool}}${{d.denied ? ' (denied)' : ''}}`;
      case 'message': return `${{d.src}} -> ${{d.dst}}: ${{d.intent}} [${{(d.artifacts||[]).join(', ')}}]`;
      case 'artifact': return `artifact ${{d.id}} (${{d.type}}) ${{d.digest?.slice(0,8)}}`;
      case 'verify': return `verify ${{d.passed ? 'PASS' : 'FAIL'}} (L${{d.level}}, exit ${{d.exit_code}})`;
      case 'converge_eval': return `round ${{e.round}}: goal=${{d.goal}} streak=${{d.streak}}/${{d.required_streak}} -> ${{d.terminal || 'continue'}}`;
      case 'escalate': return `escalate ${{d.from_model}} -> ${{d.to_model}} (round ${{d.round}})`;
      case 'stop_decision': return `stop: ${{d.terminal}} / ${{d.reason}} / ${{d.failure_class}}`;
      case 'guard_trip': return `GUARD: ${{d.rule}}`;
      case 'round_start': return `round ${{e.round}} begins`;
      default: return e.type;
    }}
  }};
  const render = () => {{
    list.innerHTML = events.map((e, i) => `<div class="event ${{i===index?'selected':''}}" data-i="${{i}}"><span class="seq">${{e.seq}}</span><span><span class="type">${{esc(e.type)}}</span> <span class="node">${{esc(e.node||'')}}</span><br>${{esc(describe(e))}}</span></div>`).join('');
    const e = events[index];
    if (e) {{
      document.getElementById('raw').textContent = JSON.stringify(e, null, 2);
      const d = e.data || {{}};
      document.getElementById('detail').innerHTML = [
        ['seq', e.seq], ['type', e.type], ['node', e.node || '-'], ['round', e.round], ['ts', e.ts]
      ].map(([k,v]) => `<dt>${{k}}</dt><dd>${{esc(v)}}</dd>`).join('');
      const active = document.querySelectorAll('.gnode.active');
      active.forEach(n => n.classList.remove('active'));
      if (e.node) {{
        const g = document.querySelector(`.gnode[data-node="${{CSS.escape(e.node)}}"]`);
        if (g) g.classList.add('active');
      }}
      const t0 = events[0] ? new Date(events[0].ts).getTime() : 0;
      const dt = (new Date(e.ts).getTime() - t0) / 1000;
      document.getElementById('clock').textContent = dt.toFixed(1) + 's';
    }}
    const d = events[index]?.data || {{}};
    document.getElementById('stateview').innerHTML = [
      ['round', events[index]?.round ?? 0],
      ['terminal', d.terminal || d.status || '-'],
      ['goal', d.goal === undefined ? '-' : String(d.goal)],
      ['reason', d.reason || '-'],
      ['failure class', d.failure_class || '-'],
      ['counters', d.counters ? JSON.stringify(d.counters) : '-']
    ].map(([k,v]) => `<dt>${{k}}</dt><dd>${{esc(v)}}</dd>`).join('');
    document.getElementById('position').textContent = `${{index+1}} / ${{events.length}}`;
    document.getElementById('prev').disabled = index === 0;
    document.getElementById('next').disabled = index === events.length - 1;
    slider.value = index;
    const selected = list.querySelector('.event.selected');
    if (selected) selected.scrollIntoView({{block:'nearest'}});
  }};
  const go = (i) => {{ index = Math.max(0, Math.min(events.length - 1, i)); render(); }};
  document.getElementById('next').onclick = () => go(index + 1);
  document.getElementById('prev').onclick = () => go(index - 1);
  slider.oninput = (e) => go(Number(e.target.value));
  document.getElementById('play').onclick = (ev) => {{
    if (timer) {{ clearInterval(timer); timer = null; ev.target.textContent = 'Play'; return; }}
    ev.target.textContent = 'Pause';
    timer = setInterval(() => {{
      if (index >= events.length - 1) {{ clearInterval(timer); timer = null; ev.target.textContent = 'Play'; return; }}
      go(index + 1);
    }}, 500);
  }};
  document.addEventListener('keydown', (ev) => {{
    if (ev.key === 'ArrowRight') go(index + 1);
    if (ev.key === 'ArrowLeft') go(index - 1);
    if (ev.key === ' ') {{ ev.preventDefault(); document.getElementById('play').click(); }}
  }});
  list.onclick = (ev) => {{
    const row = ev.target.closest('.event');
    if (row) go(Number(row.dataset.i));
  }};
  render();
}})();
</script>
</body>
</html>
"""


def write_replay(
    run_dir: Path,
    state: RunState,
    spec: WorkflowSpec,
    events: list[dict[str, Any]],
) -> str:
    payload = {
        "run_id": state.run_id,
        "workflow": spec.name,
        "terminal": state.terminal,
        "spec": spec.summary(),
        "events": events,
    }
    safe_payload = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    content = _TEMPLATE.format(
        run_id=html.escape(state.run_id),
        workflow=html.escape(spec.name),
        terminal=html.escape(str(state.terminal or "running")),
        event_count=len(events),
        max_index=max(0, len(events) - 1),
        graph_svg=_graph_svg(spec),
        payload=safe_payload,
    )
    path = run_dir / "replay.html"
    path.write_text(content, encoding="utf-8", newline="\n")
    return str(path)
