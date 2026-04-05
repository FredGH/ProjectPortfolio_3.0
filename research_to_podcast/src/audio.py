import asyncio
import os
import re
import tempfile
from datetime import datetime, timezone

import edge_tts
from pydub import AudioSegment

from src.logger import PipelineLogger

# Two neural voices assigned in order of first speaker appearance
VOICE_POOL = ["en-US-GuyNeural", "en-US-JennyNeural"]

# Silence (ms) inserted between each dialogue line for natural pacing
PAUSE_MS = 400


def generate_podcast_audio(script: str, logger: PipelineLogger) -> str:
    """
    Parse a script, synthesise each line with edge-tts, concatenate with
    pydub, and write a single MP3 to output/. Returns the output file path.

    Handles both HOST 1/HOST 2 and named speaker (**Jamie:** / **Priya:**)
    formats. The first two distinct speakers are mapped to the two voices
    in order of appearance.
    """
    os.makedirs("output", exist_ok=True)
    logger.log("audio_producer", "info", f"Script preview (first 300 chars): {script[:300]!r}")
    lines = _parse_script(script)
    logger.log("audio_producer", "info", f"Parsed {len(lines)} dialogue lines")

    if not lines:
        raise ValueError(
            f"No speaker lines found in script "
            f"({len(script)} chars). First 300 chars: {script[:300]!r}"
        )

    voice_map = _assign_voices(lines)
    logger.log("audio_producer", "info", f"Voice assignments: {voice_map}")

    segments: list[AudioSegment] = []
    pause = AudioSegment.silent(duration=PAUSE_MS)

    for i, (speaker, text) in enumerate(lines):
        voice = voice_map.get(speaker, VOICE_POOL[0])
        logger.log("audio_producer", "info", f"Line {i + 1}/{len(lines)} [{speaker}]: {text[:60]}…")
        mp3_bytes = asyncio.run(_synthesise(text, voice))
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(mp3_bytes)
            tmp_path = tmp.name
        segment = AudioSegment.from_mp3(tmp_path)
        os.unlink(tmp_path)
        segments.append(segment)
        segments.append(pause)

    podcast = sum(segments, AudioSegment.empty())
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = f"output/podcast_{ts}.mp3"
    podcast.export(out_path, format="mp3")
    logger.log("audio_producer", "info", f"Exported MP3: {out_path} ({len(podcast) / 1000:.1f}s)")
    return out_path


def _parse_script(script: str) -> list[tuple[str, str]]:
    """Return list of (speaker, text) tuples from a podcast script.

    Handles both HOST 1/HOST 2 labels and named speakers:
      HOST 1: text
      **HOST 1:** text
      HOST 1 (laughs): text
      **Jamie:** text
      **Priya:** *(laughing)* text
      Jamie: text
    """
    pattern = re.compile(
        r"^\**\s*"                          # optional leading **
        r"(HOST\s*[12]|[A-Z][A-Za-z]+)"    # HOST 1/2  OR  Capitalised name
        r"\s*\**"                           # optional trailing **
        r"[^:\n]*"                          # optional stage direction e.g. "(laughs)"
        r":\s*\**\s*"                       # colon, optional markdown
        r"(.+)$",                           # dialogue text
        re.MULTILINE,
    )
    results = []
    for m in pattern.finditer(script):
        raw = m.group(1).strip()
        # Normalise HOST1 → HOST 1
        speaker = re.sub(r"(?i)HOST(\d)", r"HOST \1", raw).upper() if re.match(r"(?i)HOST", raw) else raw
        text = m.group(2).strip().rstrip("*").strip()
        if text:
            results.append((speaker, text))
    return results


def _assign_voices(lines: list[tuple[str, str]]) -> dict[str, str]:
    """Map the first two distinct speakers to VOICE_POOL in order of appearance."""
    seen: dict[str, str] = {}
    for speaker, _ in lines:
        if speaker not in seen and len(seen) < len(VOICE_POOL):
            seen[speaker] = VOICE_POOL[len(seen)]
    return seen


async def _synthesise(text: str, voice: str) -> bytes:
    """Use edge-tts to synthesise text and return raw MP3 bytes."""
    communicate = edge_tts.Communicate(text, voice)
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)
