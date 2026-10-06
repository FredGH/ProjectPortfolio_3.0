"""The backends the CV Tailor can run on: Claude, Ollama on this Mac, or the
Docker Ollama service (Step 17).

The critic always runs on Claude; a backend only chooses who writes the CV.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from core.llm.task_config import load_task_config

logger = logging.getLogger(__name__)

TASK = "cv_tailoring"
DOCKER_OLLAMA_URL = "http://ollama:11434"
"""The compose `ollama` service; always reachable from the API container."""
NATIVE_OLLAMA_URL = "http://host.docker.internal:11434"
"""Ollama installed on the Mac. Must equal
`apps/api/app/dependencies.NATIVE_OLLAMA_BASE_URL`."""
_FALLBACK_CLAUDE_MODEL = "claude-sonnet-5"
_FALLBACK_LOCAL_MODEL = "llama3.1:8b"


@dataclass(frozen=True)
class Backend:
    """One place the Tailor can run.

    Attributes:
        id: `claude`, `native` or `docker`.
        label: Human label for the selector.
        provider: The gateway provider (`anthropic` or `ollama`).
        model: The model to call.
        prompt_family: The Tailor prompt family to load.
        base_url: The Ollama server's URL; None for Claude.
    """

    id: str
    label: str
    provider: str
    model: str
    prompt_family: str
    base_url: str | None


def resolve_backends(config_path: Path | None = None) -> dict[str, Backend]:
    """Build the three backends from the live `cv_tailoring` config.

    Args:
        config_path: Task-config override (tests).

    Returns:
        Backends keyed by id, in display order.
    """
    config = load_task_config(TASK, config_path)
    claude_model = (
        config.model if config.provider == "anthropic" else _FALLBACK_CLAUDE_MODEL
    )
    local_model = config.local_model or (
        config.model if config.provider == "ollama" else _FALLBACK_LOCAL_MODEL
    )
    local_family = config.local_prompt_family or "local"
    return {
        "claude": Backend(
            "claude",
            f"Claude · {claude_model}",
            "anthropic",
            claude_model,
            "claude",
            None,
        ),
        "native": Backend(
            "native",
            f"Ollama on this Mac · {local_model}",
            "ollama",
            local_model,
            local_family,
            NATIVE_OLLAMA_URL,
        ),
        "docker": Backend(
            "docker",
            f"Docker Ollama (CPU only, slow) · {local_model}",
            "ollama",
            local_model,
            local_family,
            DOCKER_OLLAMA_URL,
        ),
    }


def default_backend_id(config_path: Path | None = None) -> str:
    """Pick the backend a run uses when none is chosen.

    Args:
        config_path: Task-config override (tests).

    Returns:
        `claude` when the live config's provider is anthropic, else `docker`.
    """
    provider = load_task_config(TASK, config_path).provider
    return "claude" if provider == "anthropic" else "docker"


def check_availability(
    backend: Backend,
    *,
    anthropic_available: bool,
    http_client: httpx.Client | None = None,
    timeout: float = 2.0,
) -> tuple[bool, str]:
    """Tell whether a backend can be used right now. Never raises.

    Args:
        backend: The backend to check.
        anthropic_available: Whether an Anthropic API key is configured.
        http_client: Client for the Ollama probe (tests); a short-lived one
            is made when omitted.
        timeout: Seconds to wait for the Ollama server.

    Returns:
        `(available, detail)`.
    """
    if backend.provider == "anthropic":
        if anthropic_available:
            return True, "API key configured"
        return False, "ANTHROPIC_API_KEY is not set"
    url = f"{backend.base_url}/api/tags"
    own_client = http_client is None
    client = http_client or httpx.Client()
    try:
        response = client.get(url, timeout=timeout)
        response.raise_for_status()
        names = {m.get("name") for m in response.json().get("models", [])}
    except Exception:  # noqa: BLE001 — availability is advisory, never raises
        return False, f"not reachable at {backend.base_url}"
    finally:
        if own_client:
            client.close()
    if backend.model not in names:
        return False, f"model {backend.model} is not pulled"
    return True, "reachable, model present"


def unload_model(
    backend: Backend, *, http_client: httpx.Client | None = None, timeout: float = 10.0
) -> bool:
    """Ask an Ollama server to unload the backend's model. Never raises.

    Closing a request's connection stops Ollama generating tokens but not
    while it is still reading the prompt (minutes on CPU); unloading the
    model kills the runner process, so the CPU is freed at once. The model
    reloads on the next call.

    Args:
        backend: An Ollama backend.
        http_client: Client to use (tests); a short-lived one is made when
            omitted.
        timeout: Seconds to wait for the server.

    Returns:
        True iff the server acknowledged the unload.
    """
    if backend.base_url is None:
        return False
    own_client = http_client is None
    client = http_client or httpx.Client()
    try:
        response = client.post(
            f"{backend.base_url}/api/generate",
            json={"model": backend.model, "keep_alive": 0},
            timeout=timeout,
        )
        response.raise_for_status()
        return True
    except Exception:  # noqa: BLE001 — best effort
        logger.warning("could not unload %s from %s", backend.model, backend.base_url)
        return False
    finally:
        if own_client:
            client.close()
