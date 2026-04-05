# CLAUDE.md — research_to_podcast

## Project Overview
A 4-agent sequential pipeline that turns any topic into a two-host MP3 podcast.

```
Researcher → Reporting Analyst → Scriptwriter → Audio Producer
```

- **Researcher** — searches the web for recent developments on the topic (uses `web_search_20250305` tool via Anthropic API)
- **Reporting Analyst** — turns raw research into a structured report
- **Scriptwriter** — converts the report into a `HOST 1` / `HOST 2` dialogue script
- **Audio Producer** — synthesises each line with `edge-tts` (two neural voices) and concatenates into an MP3 via `pydub`

## Tech Stack
- Python 3.11
- Anthropic SDK (`claude-sonnet-4-6`)
- Gradio 5 (web UI with live workflow diagram)
- edge-tts (free, Microsoft neural voices, no API key)
- pydub + ffmpeg (MP3 assembly)

## Key Files
| File | Purpose |
|---|---|
| `app.py` | Gradio entry point |
| `src/agents.py` | `Agent` class + 3 Claude agents |
| `src/pipeline.py` | Sequential orchestrator with status callbacks |
| `src/audio.py` | TTS synthesis + MP3 export |
| `src/logger.py` | JSON-lines logger to `logs/` |
| `Dockerfile` | Single image for local Docker + Hugging Face Spaces |
| `docs/SOP.md` | Full implementation SOP |

## Setup (local, no Docker)
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY
python app.py          # http://localhost:7860
```

## Run with Docker (local)
```bash
docker build -t research-to-podcast .

docker run --rm -p 7860:7860 \
  -e ANTHROPIC_API_KEY=sk-... \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/output:/app/output \
  research-to-podcast
# open http://localhost:7860
```

## Deploy to Hugging Face Spaces
1. Create a new Space — SDK: **Docker**
2. Clone the Space repo and copy this project into it
3. Add `ANTHROPIC_API_KEY` as a Space Secret (Settings → Variables and secrets)
4. `git push` — HF builds and deploys automatically

## Code Quality
```bash
ruff check . && isort . && black .
```

## Output Locations
- `logs/pipeline_<timestamp>.log` — JSON-lines execution log (viewable in the Log tab)
- `output/podcast_<timestamp>.mp3` — generated podcast audio
