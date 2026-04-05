"""
Generates a sample podcast MP3 from a hardcoded named-speaker script.

Uses edge-tts (Microsoft neural voices) — no Anthropic API calls, no tokens consumed.
Output is written to output/podcast_<timestamp>.mp3
"""

from src.audio import generate_podcast_audio
from src.logger import PipelineLogger

NAMED_SPEAKER_SCRIPT = """\
**Jamie:** Welcome back to Data & Chill, the podcast where we make spreadsheets sound sexy. I'm Jamie.

**Priya:** And I'm Priya, the person who keeps Jamie from turning every conversation into a TED talk.

**Jamie:** *(laughing)* That's fair. That is genuinely fair.

**Priya:** So today, we are diving deep into UK data job trends in 2025.

**Jamie:** Wild is an understatement. We're talking salaries, AI taking over, regional uprisings—

**Priya:** Regional uprisings. Jamie, it's a labour market report, not Game of Thrones.

**Jamie:** *(laughing)* Have you SEEN what's happening in the North East?

**Priya:** Right, so the mean salary for data roles right now is £50,412.

**Jamie:** Until next time, keep your pipelines clean and your models honest.

**Priya:** *(laughing)* Oh god. Goodbye everyone."""

if __name__ == "__main__":
    logger = PipelineLogger("audio_sample")
    print("Synthesising audio… (this takes ~30–60s depending on script length)")
    out_path = generate_podcast_audio(NAMED_SPEAKER_SCRIPT, logger)
    print(f"Done. MP3 saved to: {out_path}")
