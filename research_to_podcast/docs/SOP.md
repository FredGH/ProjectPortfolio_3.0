# Plan: research_to_podcast Multi-Agent Project

## Context
Create a new project under `projects/` that implements a 3-agent linear pipeline (Researcher → Reporting Analyst → Scriptwriter) that turns any topic into a podcast script. Framework: **pure Anthropic SDK** (already in pyproject.toml). A **Gradio web UI** serves as the entry point with per-agent status cards and a structured **log file** that records every agent's activity.

---

## Directory Layout

```
research_to_podcast/
├── CLAUDE.md
├── CLAUDE.local.md
├── .claude/
│   ├── settings.json / settings.local.json
│   ├── agents/  commands/  rules/  skills/   ← all copied from template
├── src/
│   ├── __init__.py
│   ├── agents.py       ← Agent class + 3 agent configs
│   ├── pipeline.py     ← sequential orchestrator, emits status events
│   ├── audio.py        ← edge-tts + pydub MP3 producer (4th step)
│   └── logger.py       ← file logger utility
├── output/             ← generated MP3s land here
├── logs/               ← JSON-line log files
├── app.py              ← Gradio UI (entry point)
├── Dockerfile
├── .dockerignore
├── requirements.txt
└── .env.example
```

---

## Step 1 — `src/logger.py`
Simple structured logger that writes to `logs/pipeline_<timestamp>.log`:
- `log(agent_name, event, message)` — writes timestamped JSON lines: `{"ts": ..., "agent": ..., "event": "start|output|complete|error", "message": ...}`
- Returns the log file path so the UI can display it
- Logs directory auto-created on first write

---

## Step 2 — `src/agents.py`
`Agent` class:
- Constructor: `name`, `role`, `goal`, `backstory`, optional `tools`
- System prompt built from the 3 persona fields
- `run(user_prompt, logger) -> str`:
  - Calls `logger.log(name, "start", prompt_summary)`
  - Calls `anthropic.Anthropic().messages.create()` (streaming disabled for simplicity)
  - Researcher gets `[{"type": "web_search_20250305"}]` — live web data, no extra API keys
  - On success: `logger.log(name, "complete", char_count)`
  - On exception: `logger.log(name, "error", str(e))` then re-raises
  - Returns response text

Three module-level agent instances: `researcher`, `reporting_analyst`, `scriptwriter`

---

## Step 3 — `src/pipeline.py`
`run(topic, status_callback, logger) -> dict`:
- `status_callback(agent_name, state, message)` — called by the pipeline so the UI can update in real time; states: `"running"`, `"done"`, `"failed"`
- Sequential execution:
  1. Researcher → raw research text
  2. Reporting Analyst → structured report
  3. Scriptwriter → podcast script (formatted as `HOST 1:` / `HOST 2:` dialogue)
  4. Audio Producer → MP3 file path
- Any exception is caught per-step: calls `status_callback(name, "failed", error_detail)`, logs verbosely, then propagates so the UI can stop cleanly

---

## Step 4 — `app.py` (Gradio UI)
Layout — single page:
```
[Topic input]  [Run button]

Workflow Diagram (gr.HTML):
  [ Researcher ] ──▶ [ Reporting Analyst ] ──▶ [ Scriptwriter ] ──▶ [ Audio Producer ]
   (blue=running,        (same)                   (same)               (same)
    green=done,
    red=failed)

  Click any node → detail panel slides open showing:
    • Status, start time, end time, duration
    • Output preview (first 300 chars)
    • Full error/traceback if failed

Tabs:
  [Research]  [Report]  [Podcast Script]  [Log File]

  [⬇ Download MP3]  ← gr.DownloadButton, hidden until Audio Producer completes
```

Implementation:
- `gr.Textbox` for topic input
- Three `gr.Textbox` status fields (one per agent) updated via `gr.update()`
- Three output `gr.Textbox`/`gr.Markdown` tabs for results
- One `gr.Textbox` tab showing the raw log file path + its contents
- Button triggers a Python generator function that `yield`s UI state updates after each agent completes
- Uses Gradio's `queue()` + generator pattern so the UI refreshes after every agent step (no full-page reload)
- On failure: the failed agent's status field turns red with the full exception traceback; remaining agents show "Skipped"

---

## Step 5 — `src/audio.py` (Audio Producer — 4th pipeline step)
Converts the podcast script into a two-voice MP3 using **`edge-tts`** (free, no API key, uses Microsoft Edge neural voices):

- Parse script for speaker-labelled lines: `HOST 1: ...` / `HOST 2: ...`
- Map each host to a distinct neural voice:
  - Host 1 → `en-US-GuyNeural` (male)
  - Host 2 → `en-US-JennyNeural` (female)
- For each line: call `edge_tts.Communicate(text, voice).save(tmp_file)` → produces individual `.mp3` segments
- Concatenate all segments in order using **`pydub`** → export final `output/podcast_<timestamp>.mp3`
- `logger.log("audio_producer", ...)` at each stage
- Returns path to final MP3

> `edge-tts` is free, requires no API key, and has 400+ neural voices across languages/genders. `pydub` concatenates audio; requires `ffmpeg` (installed in Docker).

The **scriptwriter agent prompt** is updated to explicitly format all dialogue as:
```
HOST 1: <line>
HOST 2: <line>
```
so the audio parser can split reliably.

---

## Step 6 — `requirements.txt`
```
anthropic>=0.49.0
gradio>=5.0.0
python-dotenv>=1.0.0
edge-tts>=6.1.9
pydub>=0.25.1
```

---

## Step 7 — `Dockerfile` (local + Hugging Face compatible)
Single Dockerfile works for both targets:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p logs output
EXPOSE 7860
ENV GRADIO_SERVER_NAME=0.0.0.0
ENV GRADIO_SERVER_PORT=7860
CMD ["python", "app.py"]
```
- `ffmpeg` installed via apt — required by `pydub` for MP3 encoding
- Port 7860 required by Hugging Face Spaces Docker SDK
- `output/` directory pre-created for MP3 files

## Step 7 — `.dockerignore`
```
venv/
__pycache__/
*.pyc
logs/
.env
.env.example
.git/
```

---

## How to Run Locally with Docker

```bash
cd projects/research_to_podcast

# 1. Build the image
docker build -t research-to-podcast .

# 2. Run — pass your API key as an env var (never bake it into the image)
docker run --rm -p 7860:7860 \
  -e ANTHROPIC_API_KEY=your_key_here \
  -v $(pwd)/logs:/app/logs \
  research-to-podcast

# 3. Open browser at http://localhost:7860
```
- `-v $(pwd)/logs:/app/logs` mounts a local folder so log files persist after the container exits
- The `.env` file is intentionally excluded from the image; pass secrets only via `-e`

---

## How to Deploy to Hugging Face Spaces

```bash
# 1. Create a new Space on hf.co with SDK = "Docker"
# 2. Clone the Space repo
git clone https://huggingface.co/spaces/<your-username>/<space-name>

# 3. Copy project files into the cloned repo (exclude .env, venv, logs)
cp -r projects/research_to_podcast/. <space-name>/

# 4. Add ANTHROPIC_API_KEY as a Space Secret in the HF UI
#    (Settings → Variables and secrets → New secret)

# 5. Push — HF builds and deploys automatically
cd <space-name>
git add . && git commit -m "Initial deploy"
git push
```
Notes:
- HF Spaces injects secrets as env vars at runtime — `python-dotenv` will pick up `ANTHROPIC_API_KEY` automatically
- `logs/` writes to the container's ephemeral filesystem on HF (no persistence between restarts — acceptable for a course project)
- The `docker-deploy` skill in `.claude/skills/` can automate most of the above steps

---

## Key Design Decisions
| Decision | Choice | Reason |
|---|---|---|
| Framework | Raw Anthropic SDK | Already installed, zero extra deps |
| Web search | `web_search_20250305` (built-in Claude tool) | Live data, no extra API keys |
| UI | Gradio `gr.HTML` + generator + `queue()` | Workflow nodes with colors; real-time updates |
| Workflow nodes | Custom HTML/CSS/JS in `gr.HTML` | Gradio has no native graph component; HTML gives full control |
| Node click detail | Client-side JS toggle within `gr.HTML` | No Python callback needed; detail data embedded in HTML on each yield |
| TTS | `edge-tts` | Free, no API key, 400+ neural voices, two distinct hosts |
| Audio concat | `pydub` + `ffmpeg` | Simple, reliable MP3 assembly |
| Logging | JSON-lines to `logs/` dir | Machine-readable, easy to display raw in UI; mounted volume for local Docker |
| Error display | Full traceback in node detail panel + log tab | Verbose as requested |
| Model | `claude-sonnet-4-6` for all agents | Fast + capable for research/writing tasks |
| Docker port | 7860 | Gradio default; required by HF Spaces Docker SDK |
| Secrets | Env var only, never in image | Security best practice |

---

## Verification

**Local (no Docker):**
```bash
cd projects/research_to_podcast
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY
python app.py          # http://localhost:7860
```

**Local Docker:**
```bash
docker build -t research-to-podcast .
docker run --rm -p 7860:7860 -e ANTHROPIC_API_KEY=sk-... -v $(pwd)/logs:/app/logs research-to-podcast
# http://localhost:7860
```

Enter a topic, click Run, watch each agent card update live. On success all three tabs populate. On error the failed agent shows the full traceback.
