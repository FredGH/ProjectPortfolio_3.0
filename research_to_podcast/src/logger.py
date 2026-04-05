import json
import os
from datetime import datetime, timezone


class PipelineLogger:
    def __init__(self, run_id: str):
        os.makedirs("logs", exist_ok=True)
        self.path = f"logs/pipeline_{run_id}.log"
        self._write({"ts": _now(), "agent": "pipeline", "event": "init", "message": f"Log started: {self.path}"})

    def log(self, agent: str, event: str, message: str) -> None:
        self._write({"ts": _now(), "agent": agent, "event": event, "message": message})

    def read(self) -> str:
        try:
            with open(self.path) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def _write(self, record: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
