# Research to Podcast

A 4-agent sequential pipeline that turns any topic into a two-host MP3 podcast, powered by the Anthropic SDK and a Gradio web UI.

```
Researcher → Reporting Analyst → Scriptwriter → Audio Producer
```

---

## How it works

| Agent | Role | Model |
|---|---|---|
| **Researcher** | Searches the web for recent developments on the topic using the `web_search_20250305` built-in tool | claude-sonnet-4-6 |
| **Reporting Analyst** | Turns raw research into a structured, sectioned report | claude-sonnet-4-6 |
| **Scriptwriter** | Converts the report into a two-host dialogue podcast script | claude-sonnet-4-6 |
| **Audio Producer** | Synthesises each line with `edge-tts` (two distinct neural voices) and concatenates into an MP3 via `pydub` | — (no LLM) |

The UI supports two research flows:
- **Auto** — the Researcher agent searches the web live (consumes ~30–100k input tokens)
- **Manual** — paste your own research text; the Researcher is bypassed, saving significant token cost

---

## Stack

| Component | Library |
|---|---|
| LLM agents | `anthropic` SDK (`claude-sonnet-4-6`) |
| Web UI | `gradio` 5+ |
| Text-to-speech | `edge-tts` (Microsoft neural voices, free, no API key) |
| Audio assembly | `pydub` + `ffmpeg` |
| Environment | `python-dotenv` |

---

## Project structure

```
research_to_podcast/
├── app.py                  # Gradio entry point
├── src/
│   ├── agents.py           # Agent class + 3 Claude agent instances
│   ├── pipeline.py         # Sequential orchestrator with status callbacks
│   ├── audio.py            # TTS synthesis + MP3 export
│   └── logger.py           # JSON-lines logger → logs/
├── tests/
│   ├── test_agents.py      # 26 unit tests (0 real API calls)
│   └── test_audio.py       # 21 unit tests (no TTS calls)
├── generate_audio_sample.py # Standalone script: generate MP3 from a fixture script
├── docs/
│   └── SOP.md              # Full implementation SOP
├── output/                 # Generated MP3s (gitignored)
├── logs/                   # JSON-lines pipeline logs (gitignored)
├── Dockerfile
└── requirements.txt
```

---

## Setup (local, no Docker)

```bash
# 1. Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# 2. Install dependencies (requires ffmpeg on the host)
brew install ffmpeg          # macOS
# apt-get install ffmpeg     # Linux
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env         # then add your ANTHROPIC_API_KEY

# 4. Launch
python app.py                # http://localhost:7860
```

---

## Run with Docker (local)

```bash
docker build -t research-to-podcast .

docker run --rm -p 7860:7860 \
  -e ANTHROPIC_API_KEY=sk-... \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/output:/app/output \
  research-to-podcast
```

Open [http://localhost:7860](http://localhost:7860).

> ffmpeg is installed automatically inside the Docker image — no host dependency needed.

---

## Deploy to Hugging Face Spaces

1. Create a new Space — SDK: **Docker**
2. Clone the Space repo and copy this project into it
3. Add `ANTHROPIC_API_KEY` as a Space Secret (Settings → Variables and secrets)
4. `git push` — HF builds and deploys automatically

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** Your Anthropic API key |
| `MAX_TOKENS_RESEARCHER` | `2048` | Max output tokens for the Researcher |
| `MAX_TOKENS_ANALYST` | `2048` | Max output tokens for the Reporting Analyst |
| `MAX_TOKENS_SCRIPTWRITER` | `3000` | Max output tokens for the Scriptwriter |
| `MAX_CONTEXT_CHARS` | `6000` | Input truncation threshold (chars) |
| `MAX_TOPIC_LENGTH` | `200` | Max topic length accepted by the UI |
| `AGENT_DELAY_SECONDS` | `65` | Cooldown between LLM steps (rate-limit guard) |
| `TOKEN_WARN_THRESHOLD` | `8000` | Total token count that triggers a UI warning |
| `PRICE_INPUT_PER_MTOK` | `3.00` | Input token price per million (USD) |
| `PRICE_OUTPUT_PER_MTOK` | `15.00` | Output token price per million (USD) |

---

## Run tests

```bash
source venv/bin/activate
python -m unittest discover -v
```

47 tests, ~0.05s, zero API calls — all agents are mocked via `patch.object`.

---

## Generate a sample MP3 (no API key needed)

```bash
python generate_audio_sample.py
# Output: output/podcast_<timestamp>.mp3
```

Uses a hardcoded script fixture; only `edge-tts` is invoked.

---

## Token cost reference

Based on observed runs (topic: *UK data job trends 2025*):

| Agent | Input tokens | Output tokens |
|---|---|---|
| Researcher (Auto) | 38,000 – 101,000 | ~1,700 – 2,300 |
| Reporting Analyst | ~1,300 | ~2,000 |
| Scriptwriter | ~1,500 | ~3,000 |
| Audio Producer | 0 | 0 |

Using Manual flow bypasses the Researcher entirely, reducing cost by ~95%.
