# ProjectPortfolio 3.0

A portfolio of AI agent projects built with the Anthropic SDK and Claude.

---

## Projects

### 🎙️ [research_to_podcast](research_to_podcast/)

A 4-agent sequential pipeline that turns any topic into a two-host MP3 podcast.

```
Researcher ──▶ Reporting Analyst ──▶ Scriptwriter ──▶ Audio Producer
```

| Agent | What it does |
|---|---|
| **Researcher** | Searches the web for recent developments on the topic (live web search via Claude) |
| **Reporting Analyst** | Turns raw research into a structured report |
| **Scriptwriter** | Converts the report into a natural `HOST 1` / `HOST 2` dialogue script |
| **Audio Producer** | Synthesises each line with `edge-tts` (two neural voices) and assembles an MP3 |

**Tech:** Python 3.11 · Anthropic SDK (`claude-sonnet-4-6`) · Gradio 5 · edge-tts · pydub · Docker

**Features:**
- Gradio web UI with a live workflow diagram — nodes turn blue (running), green (done), red (failed)
- Click any node to inspect start/end time, duration, output preview, token usage, and errors
- Live token counter and USD cost display, updated after each agent step
- Token guardrails: per-agent `max_tokens` caps, input truncation, topic length limit
- Downloadable MP3 at the end
- Docker-ready for local use and Hugging Face Spaces deployment

**Quick start:**
```bash
cd research_to_podcast
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY
python app.py          # → http://localhost:7860
```

**Docker:**
```bash
cd research_to_podcast
docker build -t research-to-podcast .
docker run --rm -p 7860:7860 \
  -e ANTHROPIC_API_KEY=sk-... \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/output:/app/output \
  research-to-podcast


Then open http://localhost:7860
[Optional] If port is already in use then: sof -ti :7860 | xargs kill -9 2>/dev/null; sleep 1; lsof -ti :7860 || echo "Port 7860 is now free"
```

---

## Shared Resources

| Folder | Purpose |
|---|---|
| [`claude_project_template/`](claude_project_template/) | Reusable Claude Code project scaffold — agents, rules, commands, skills |
| [`implementation_templates/`](implementation_templates/) | SOP and technical proposal templates |

---

## Repository Layout

```
ProjectPortfolio_3.0/
├── research_to_podcast/     ← AI podcast pipeline project
│   ├── src/                 ← agents, pipeline, audio, logger
│   ├── app.py               ← Gradio entry point
│   ├── Dockerfile
│   ├── requirements.txt
│   └── docs/SOP.md          ← full implementation SOP
├── claude_project_template/ ← reusable Claude Code scaffold
└── implementation_templates/
```
