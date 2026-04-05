"""
Research to Podcast — Gradio web application
Run: python app.py  (from projects/ProjectPortfolio_3.0/research_to_podcast/)
"""

import os
import queue
import threading
from datetime import datetime, timezone

import gradio as gr
from dotenv import load_dotenv

from src import pipeline as _pipeline
from src.logger import PipelineLogger

# Module-level stop event — replaced on each run
_stop_event: threading.Event = threading.Event()


def _abort_pipeline():
    _stop_event.set()

load_dotenv()

# ---------------------------------------------------------------------------
# Guardrail + pricing config
# ---------------------------------------------------------------------------
MAX_TOPIC_LENGTH = int(os.getenv("MAX_TOPIC_LENGTH", "200"))
TOKEN_WARN_THRESHOLD = int(os.getenv("TOKEN_WARN_THRESHOLD", "8000"))
PRICE_INPUT_PER_MTOK = float(os.getenv("PRICE_INPUT_PER_MTOK", "3.00"))
PRICE_OUTPUT_PER_MTOK = float(os.getenv("PRICE_OUTPUT_PER_MTOK", "15.00"))


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * PRICE_INPUT_PER_MTOK + output_tokens * PRICE_OUTPUT_PER_MTOK) / 1_000_000


# ---------------------------------------------------------------------------
# Node definitions
# ---------------------------------------------------------------------------
NODES = [
    {"id": "researcher",        "label": "Researcher"},
    {"id": "reporting_analyst", "label": "Reporting Analyst"},
    {"id": "scriptwriter",      "label": "Scriptwriter"},
    {"id": "audio_producer",    "label": "Audio Producer"},
]


def _empty_states() -> dict:
    return {n["id"]: {"state": "idle", "message": "", "meta": {}} for n in NODES}


def _node_color(state: str) -> str:
    return {
        "idle": "#64748b", "running": "#3b82f6", "done": "#22c55e",
        "failed": "#ef4444", "skipped": "#94a3b8", "aborted": "#f97316",
        "bypassed": "#06b6d4",
    }.get(state, "#64748b")


def _node_icon(state: str) -> str:
    return {
        "idle": "⬜", "running": "⏳", "done": "✅",
        "failed": "❌", "skipped": "⏭️", "aborted": "🛑",
        "bypassed": "📋",
    }.get(state, "⬜")


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# Token / cost bar HTML
# ---------------------------------------------------------------------------

def _render_token_bar(cumulative: dict) -> str:
    inp = cumulative.get("input_tokens", 0)
    out = cumulative.get("output_tokens", 0)
    total = inp + out
    cost = _calc_cost(inp, out)
    warn = total > TOKEN_WARN_THRESHOLD

    warn_badge = (
        f'<span style="margin-left:12px;background:#f59e0b;color:#1c1917;'
        f'border-radius:6px;padding:2px 8px;font-size:11px;font-weight:700">'
        f'⚠ >{TOKEN_WARN_THRESHOLD:,} token threshold</span>'
        if warn else ""
    )

    bar_color = "#f59e0b" if warn else "#3b82f6"

    return f"""
    <div style="background:#0f172a;border-radius:10px;padding:12px 18px;
                display:flex;align-items:center;gap:16px;flex-wrap:wrap;font-size:13px;color:#e2e8f0">
      <span style="color:{bar_color};font-weight:700;font-size:15px">⚡ Tokens</span>
      <span>Input: <b>{inp:,}</b></span>
      <span>Output: <b>{out:,}</b></span>
      <span>Total: <b>{total:,}</b></span>
      <span style="margin-left:8px;color:#4ade80;font-weight:700">Cost: ${cost:.4f}</span>
      {warn_badge}
    </div>"""


# ---------------------------------------------------------------------------
# Workflow diagram HTML
# ---------------------------------------------------------------------------

def _render_workflow(states: dict) -> str:
    nodes_html = ""
    for i, node in enumerate(NODES):
        nid = node["id"]
        ns = states[nid]
        color = _node_color(ns["state"])
        icon = _node_icon(ns["state"])
        label = node["label"]

        meta = ns.get("meta", {})
        start = meta.get("start", "")
        end = meta.get("end", "")
        preview = meta.get("output_preview", "")
        error = meta.get("error", "")
        msg = ns.get("message", "")
        step_usage = meta.get("step_usage", {})

        def _row(k, v):
            return (
                f'<tr><td style="color:#94a3b8;padding:2px 8px 2px 0;white-space:nowrap">{k}</td>'
                f'<td style="word-break:break-word">{v}</td></tr>'
            )

        detail_rows = _row("Status", f"{icon} {ns['state'].upper()}")
        if msg:
            detail_rows += _row("Info", msg)
        if start:
            detail_rows += _row("Started", start)
        if end:
            detail_rows += _row("Finished", end)
        if start and end:
            try:
                from datetime import datetime as _dt
                s = _dt.fromisoformat(start)
                e = _dt.fromisoformat(end)
                detail_rows += _row("Duration", f"{(e - s).total_seconds():.1f}s")
            except Exception:
                pass
        if step_usage:
            inp = step_usage.get("input_tokens", 0)
            out = step_usage.get("output_tokens", 0)
            cost = _calc_cost(inp, out)
            detail_rows += _row("Tokens (this step)", f"in={inp:,} | out={out:,} | <b style='color:#4ade80'>${cost:.4f}</b>")
        if preview:
            detail_rows += _row(
                "Output preview",
                f'<pre style="white-space:pre-wrap;margin:0;font-size:11px">{_esc(preview)}</pre>',
            )
        if error:
            detail_rows += _row(
                "Error",
                f'<pre style="white-space:pre-wrap;margin:0;font-size:11px;color:#fca5a5">{_esc(error)}</pre>',
            )

        detail_html = (
            f'<div id="detail-{nid}" style="display:none;margin-top:8px;background:#1e293b;'
            f'border-radius:8px;padding:10px;text-align:left;font-size:12px">'
            f'<table style="width:100%;border-collapse:collapse">{detail_rows}</table></div>'
        )

        arrow = ""
        if i < len(NODES) - 1:
            arrow = (
                '<div style="display:flex;align-items:center;color:#94a3b8;font-size:22px;'
                'padding:0 4px;margin-top:-18px">&#10140;</div>'
            )

        nodes_html += f"""
        <div style="display:flex;flex-direction:column;align-items:center">
          <div onclick="toggleDetail('{nid}')" style="
              cursor:pointer;background:{color};border-radius:12px;padding:14px 22px;
              min-width:130px;text-align:center;color:#fff;font-weight:600;font-size:14px;
              box-shadow:0 2px 8px rgba(0,0,0,0.4);transition:transform .1s;user-select:none"
            onmouseover="this.style.transform='scale(1.04)'"
            onmouseout="this.style.transform='scale(1)'">
            <div style="font-size:20px;margin-bottom:4px">{icon}</div>
            {label}
          </div>
          {detail_html}
        </div>
        {arrow}"""

    toggle_js = """
    <script>
    function toggleDetail(id) {
      var el = document.getElementById('detail-' + id);
      if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }
    </script>"""

    return f"""
    <div style="background:#0f172a;border-radius:16px;padding:24px 20px;margin-bottom:8px">
      <div style="display:flex;align-items:flex-start;justify-content:center;flex-wrap:wrap;gap:8px">
        {nodes_html}
      </div>
    </div>
    {toggle_js}"""


# ---------------------------------------------------------------------------
# Pipeline runner — generator for Gradio streaming
# ---------------------------------------------------------------------------
# Outputs order (9 items):
#   workflow_html, token_bar, research_out, report_out, script_out, log_out,
#   download_btn, run_btn, stop_btn

_IDLE_OUTPUTS = (
    _render_workflow(_empty_states()),
    _render_token_bar({}),
    "", "", "", "",
    gr.update(visible=False, value=None),
    gr.update(interactive=True),   # run_btn
    gr.update(visible=False),      # stop_btn
)


def run_pipeline(topic: str, flow: str, research_input: str):
    global _stop_event

    manual = flow == "manual"
    research_override = research_input.strip() if manual and research_input.strip() else None

    # -- Guardrails ----------------------------------------------------------
    topic = topic.strip()

    # In manual mode the topic box is hidden — use a neutral fallback so the
    # analyst / scriptwriter prompts still make sense.
    if manual and not topic:
        topic = "the provided research"

    if not manual and not topic:
        yield _IDLE_OUTPUTS
        return

    if not manual and len(topic) > MAX_TOPIC_LENGTH:
        err_html = (
            f'<div style="background:#450a0a;color:#fca5a5;border-radius:10px;padding:12px 18px;'
            f'font-weight:600">⛔ Topic too long ({len(topic)} chars). '
            f'Maximum allowed: {MAX_TOPIC_LENGTH} characters.</div>'
        )
        yield (
            err_html,
            _render_token_bar({}),
            "", "", "", "",
            gr.update(visible=False, value=None),
            gr.update(interactive=True),
            gr.update(visible=False),
        )
        return

    if manual and not research_override:
        yield (
            '<div style="background:#450a0a;color:#fca5a5;border-radius:10px;padding:12px 18px;'
            'font-weight:600">⛔ Please paste your research text before running in Manual mode.</div>',
            _render_token_bar({}),
            "", "", "", "",
            gr.update(visible=False, value=None),
            gr.update(interactive=True),
            gr.update(visible=False),
        )
        return

    # Reset stop event for this run
    _stop_event = threading.Event()

    states = _empty_states()
    pipeline_result: dict = {}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    logger = PipelineLogger(run_id)
    update_q: queue.Queue = queue.Queue()
    latest_usage: dict = {}

    # Tracks output content per tab — populated as each agent finishes
    live_results: dict = {"research": "", "report": "", "script": ""}
    _AGENT_TO_KEY = {
        "researcher": "research",
        "reporting_analyst": "report",
        "scriptwriter": "script",
    }

    def _streaming_callback(agent_name, state, message, meta):
        states[agent_name] = {"state": state, "message": message, "meta": meta}
        cumulative = meta.get("cumulative_usage", {})
        latest_usage.update(cumulative)
        # Populate the relevant tab as soon as its agent reports done
        if state == "done" and agent_name in _AGENT_TO_KEY:
            live_results[_AGENT_TO_KEY[agent_name]] = meta.get("output", "")
        update_q.put((dict(states), dict(cumulative)))

    def _run_streaming():
        try:
            pipeline_result.update(
                _pipeline.run(topic, _streaming_callback, logger, _stop_event, research_override)
            )
        except Exception:
            pass
        finally:
            update_q.put(None)

    t = threading.Thread(target=_run_streaming, daemon=True)
    t.start()

    # Show stop button, disable run button while pipeline is active
    yield (
        _render_workflow(states),
        _render_token_bar({}),
        "", "", "", "",
        gr.update(visible=False, value=None),
        gr.update(interactive=False),  # run_btn
        gr.update(visible=True),       # stop_btn
    )

    while True:
        item = update_q.get()
        if item is None:
            break
        snap, usage_snap = item
        yield (
            _render_workflow(snap),
            _render_token_bar(usage_snap),
            live_results["research"],
            live_results["report"],
            live_results["script"],
            logger.read(),
            gr.update(visible=False, value=None),
            gr.update(interactive=False),
            gr.update(visible=True),
        )

    t.join()

    mp3 = pipeline_result.get("mp3_path")
    final_usage = pipeline_result.get("usage", latest_usage)

    yield (
        _render_workflow(states),
        _render_token_bar(final_usage),
        live_results["research"],
        live_results["report"],
        live_results["script"],
        logger.read(),
        gr.update(visible=bool(mp3), value=mp3 if mp3 else None),
        gr.update(interactive=True),   # run_btn re-enabled
        gr.update(visible=False),      # stop_btn hidden
    )


# ---------------------------------------------------------------------------
# Gradio layout
# ---------------------------------------------------------------------------

with gr.Blocks(
    title="Research to Podcast",
    theme=gr.themes.Default(primary_hue="blue"),
    css="""
    #run-btn { font-size: 16px; font-weight: 700; }
    #topic-input textarea { font-size: 15px; }
    #research-out textarea, #report-out textarea, #script-out textarea {
        height: 420px !important;
        overflow-y: auto !important;
        resize: vertical;
        font-family: monospace;
        font-size: 13px;
    }
    #log-out textarea {
        height: 280px !important;
        overflow-y: auto !important;
        resize: vertical;
        font-family: monospace;
        font-size: 12px;
    }
    #research-input textarea {
        font-size: 13px;
        font-family: monospace;
        resize: vertical;
    }
    """,
) as demo:

    gr.Markdown("# 🎙️ Research to Podcast")
    gr.Markdown(
        "Enter a topic — four agents will research it, write a report, "
        "craft a two-host podcast script, and produce an MP3.\n\n"
        f"*Topic limit: {MAX_TOPIC_LENGTH} characters. "
        f"Token warning threshold: {TOKEN_WARN_THRESHOLD:,}.*"
    )

    flow_selector = gr.Radio(
        choices=[("🔍 Auto — Researcher agent searches the web", "auto"),
                 ("📋 Manual — Paste your own research", "manual")],
        value="auto",
        label="Research flow",
        interactive=True,
    )

    topic_input = gr.Textbox(
        label=f"Topic (max {MAX_TOPIC_LENGTH} chars)",
        placeholder="e.g. Large Language Models, Quantum Computing, Climate Tech…",
        visible=True,
        elem_id="topic-input",
    )

    research_input = gr.Textbox(
        label="Research text (paste here — replaces the Researcher agent)",
        placeholder="Paste your research notes, article text, or any reference material…",
        lines=8,
        visible=False,
        elem_id="research-input",
    )

    with gr.Row():
        run_btn = gr.Button("▶ Run", variant="primary", scale=1, elem_id="run-btn")
        stop_btn = gr.Button("⏹ Stop", variant="stop", scale=1, visible=False, elem_id="stop-btn")

    # Workflow diagram
    workflow_html = gr.HTML(_render_workflow(_empty_states()))

    # Token / cost bar — live updated
    token_bar = gr.HTML(_render_token_bar({}))

    # Output tabs
    with gr.Tabs():
        with gr.Tab("Research") as research_tab:
            research_out = gr.Textbox(label="Raw Research", lines=20, interactive=False, elem_id="research-out")
        with gr.Tab("Report"):
            report_out = gr.Textbox(label="Structured Report", lines=20, interactive=False, elem_id="report-out")
        with gr.Tab("Podcast Script"):
            script_out = gr.Textbox(label="Podcast Script", lines=20, interactive=False, elem_id="script-out")
        with gr.Tab("Log"):
            log_out = gr.Textbox(label="Pipeline Log", lines=15, interactive=False, elem_id="log-out")

    flow_selector.change(
        fn=lambda f: (
            gr.update(visible=(f == "auto")),    # topic_input
            gr.update(visible=(f == "manual")),  # research_input
            gr.update(visible=(f == "auto")),    # research_tab
        ),
        inputs=[flow_selector],
        outputs=[topic_input, research_input, research_tab],
    )

    # Download button — hidden until audio is ready
    download_btn = gr.DownloadButton(
        label="⬇ Download MP3",
        visible=False,
        variant="secondary",
    )

    run_event = run_btn.click(
        fn=run_pipeline,
        inputs=[topic_input, flow_selector, research_input],
        outputs=[workflow_html, token_bar, research_out, report_out, script_out, log_out,
                 download_btn, run_btn, stop_btn],
    )

    stop_btn.click(fn=_abort_pipeline, inputs=[], outputs=[], cancels=[run_event])


if __name__ == "__main__":
    demo.queue().launch(server_name="0.0.0.0", server_port=7860, share=False)
