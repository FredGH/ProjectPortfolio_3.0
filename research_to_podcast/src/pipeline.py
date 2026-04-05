import os
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timezone

from src.agents import reporting_analyst, researcher, scriptwriter
from src.audio import generate_podcast_audio
from src.logger import PipelineLogger

STEPS = ["researcher", "reporting_analyst", "scriptwriter", "audio_producer"]

# Seconds to pause between LLM agent calls to stay within per-minute token limits.
# With a 30k input tok/min cap and ~30-40k tokens per call, 65s guarantees the
# bucket resets between steps. Lower this if you are on a higher-tier plan.
AGENT_DELAY_SECONDS = int(os.getenv("AGENT_DELAY_SECONDS", "65"))

_ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0}


class PipelineAborted(Exception):
    pass


def _add_usage(total: dict, step: dict) -> dict:
    return {
        "input_tokens": total["input_tokens"] + step.get("input_tokens", 0),
        "output_tokens": total["output_tokens"] + step.get("output_tokens", 0),
    }


def run(
    topic: str,
    status_callback: Callable[[str, str, str, dict], None],
    logger: PipelineLogger,
    stop_event: threading.Event | None = None,
    research_override: str | None = None,
) -> dict:
    """
    Execute the full research-to-podcast pipeline.

    If research_override is provided the Researcher agent is skipped and that
    text is used directly as the input to the Reporting Analyst.

    status_callback(agent_name, state, message, meta)
        state: "running" | "done" | "failed" | "skipped" | "bypassed"
        meta keys: start, end, output_preview, error,
                   step_usage, cumulative_usage
    """
    results: dict = {}
    failed = False
    cumulative_usage = dict(_ZERO_USAGE)

    def _check_abort():
        if stop_event and stop_event.is_set():
            raise PipelineAborted("Pipeline aborted by user")

    def _interruptible_sleep(seconds: int):
        """Sleep in 1-second chunks so abort is detected within ~1s."""
        for _ in range(seconds):
            if stop_event and stop_event.is_set():
                raise PipelineAborted("Pipeline aborted by user")
            time.sleep(1)

    try:
        # -- Step 1: Researcher (or bypass) -----------------------------------
        _check_abort()
        if research_override:
            results["research"] = research_override
            status_callback(
                "researcher", "bypassed",
                "Research provided by user — Researcher agent skipped.",
                {
                    "start": _now(), "end": _now(),
                    "output": research_override,
                    "output_preview": research_override[:300],
                    "cumulative_usage": dict(cumulative_usage),
                },
            )
        elif not failed:
            meta = {"start": _now(), "cumulative_usage": dict(cumulative_usage)}
            status_callback("researcher", "running", "Searching for recent information…", meta)
            try:
                topic_prompt = (
                    f"Find recent releases, articles, topics, and progress about: {topic}. "
                    "Provide a comprehensive summary of the latest developments."
                )
                results["research"], step_usage = researcher.run(topic_prompt, logger)
                cumulative_usage = _add_usage(cumulative_usage, step_usage)
                meta.update({
                    "end": _now(),
                    "output": results["research"],
                    "output_preview": results["research"][:300],
                    "step_usage": step_usage,
                    "cumulative_usage": dict(cumulative_usage),
                })
                status_callback("researcher", "done", "Research complete.", meta)
            except PipelineAborted:
                raise
            except Exception as e:
                meta.update({"end": _now(), "error": traceback.format_exc(),
                             "cumulative_usage": dict(cumulative_usage)})
                status_callback("researcher", "failed", str(e), meta)
                failed = True

        # -- Rate-limit cooldown (skipped when research was user-provided) -----
        if not failed and not research_override:
            logger.log("pipeline", "info", f"Waiting {AGENT_DELAY_SECONDS}s between agents (rate-limit guardrail)")
            status_callback(
                "reporting_analyst", "running",
                f"⏱ Waiting {AGENT_DELAY_SECONDS}s for rate-limit window to reset…",
                {"start": _now(), "cumulative_usage": dict(cumulative_usage)},
            )
            _interruptible_sleep(AGENT_DELAY_SECONDS)

        # -- Step 2: Reporting Analyst ----------------------------------------
        _check_abort()
        if not failed:
            meta = {"start": _now(), "cumulative_usage": dict(cumulative_usage)}
            status_callback("reporting_analyst", "running", "Compiling research into a structured report…", meta)
            try:
                results["report"], step_usage = reporting_analyst.run(
                    f"Create a detailed report based on the following research about {topic}:\n\n{results['research']}",
                    logger,
                )
                cumulative_usage = _add_usage(cumulative_usage, step_usage)
                meta.update({
                    "end": _now(),
                    "output": results["report"],
                    "output_preview": results["report"][:300],
                    "step_usage": step_usage,
                    "cumulative_usage": dict(cumulative_usage),
                })
                status_callback("reporting_analyst", "done", "Report complete.", meta)
            except PipelineAborted:
                raise
            except Exception as e:
                meta.update({"end": _now(), "error": traceback.format_exc(),
                             "cumulative_usage": dict(cumulative_usage)})
                status_callback("reporting_analyst", "failed", str(e), meta)
                failed = True

        # -- Rate-limit cooldown ----------------------------------------------
        if not failed:
            logger.log("pipeline", "info", f"Waiting {AGENT_DELAY_SECONDS}s between agents (rate-limit guardrail)")
            status_callback(
                "scriptwriter", "running",
                f"⏱ Waiting {AGENT_DELAY_SECONDS}s for rate-limit window to reset…",
                {"start": _now(), "cumulative_usage": dict(cumulative_usage)},
            )
            _interruptible_sleep(AGENT_DELAY_SECONDS)

        # -- Step 3: Scriptwriter ---------------------------------------------
        _check_abort()
        if not failed:
            meta = {"start": _now(), "cumulative_usage": dict(cumulative_usage)}
            status_callback("scriptwriter", "running", "Writing podcast script…", meta)
            try:
                results["script"], step_usage = scriptwriter.run(
                    (
                        f"Write a fun, engaging podcast script about {topic} based on the following report.\n\n"
                        f"{results['report']}\n\n"
                        "IMPORTANT FORMAT RULES:\n"
                        "- The podcast has exactly two hosts: HOST 1 and HOST 2.\n"
                        "- Every single line of dialogue MUST start with 'HOST 1:' or 'HOST 2:' (no exceptions).\n"
                        "- Do not include stage directions, scene headings, or any other text outside HOST lines.\n"
                        "- Keep it under 100 exchanges total.\n"
                        "- Make it natural, funny, and technically insightful."
                    ),
                    logger,
                )
                cumulative_usage = _add_usage(cumulative_usage, step_usage)
                meta.update({
                    "end": _now(),
                    "output": results["script"],
                    "output_preview": results["script"][:300],
                    "step_usage": step_usage,
                    "cumulative_usage": dict(cumulative_usage),
                })
                status_callback("scriptwriter", "done", "Script complete.", meta)
            except PipelineAborted:
                raise
            except Exception as e:
                meta.update({"end": _now(), "error": traceback.format_exc(),
                             "cumulative_usage": dict(cumulative_usage)})
                status_callback("scriptwriter", "failed", str(e), meta)
                failed = True

        # -- Step 4: Audio Producer -------------------------------------------
        if not failed:
            meta = {"start": _now(), "cumulative_usage": dict(cumulative_usage)}
            status_callback("audio_producer", "running", "Generating MP3 with two voices…", meta)
            try:
                mp3_path = generate_podcast_audio(results["script"], logger)
                results["mp3_path"] = mp3_path
                # Audio producer uses no LLM tokens — cumulative unchanged
                meta.update({
                    "end": _now(),
                    "output_preview": f"Saved to {mp3_path}",
                    "cumulative_usage": dict(cumulative_usage),
                })
                status_callback("audio_producer", "done", f"Audio saved: {mp3_path}", meta)
            except PipelineAborted:
                raise
            except Exception as e:
                meta.update({"end": _now(), "error": traceback.format_exc(),
                             "cumulative_usage": dict(cumulative_usage)})
                status_callback("audio_producer", "failed", str(e), meta)
                failed = True

    except PipelineAborted:
        failed = True
        logger.log("pipeline", "info", "Pipeline aborted by user")

    # Skipped / aborted steps
    if failed:
        aborted = stop_event is not None and stop_event.is_set()
        state = "aborted" if aborted else "skipped"
        msg = "Aborted by user." if aborted else "Skipped due to earlier failure."
        for step in STEPS:
            if step not in results and step != "mp3_path":
                status_callback(step, state, msg,
                                {"cumulative_usage": dict(cumulative_usage)})

    results["usage"] = cumulative_usage
    logger.log("pipeline", "usage",
               f"Total — in={cumulative_usage['input_tokens']} out={cumulative_usage['output_tokens']} tokens")
    return results


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
