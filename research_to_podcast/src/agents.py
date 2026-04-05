import os
import time
import traceback

import anthropic
from dotenv import load_dotenv

from src.logger import PipelineLogger

load_dotenv()

MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Guardrail config — read from env with safe defaults
# ---------------------------------------------------------------------------
MAX_TOKENS_RESEARCHER = int(os.getenv("MAX_TOKENS_RESEARCHER", "2048"))
MAX_TOKENS_ANALYST = int(os.getenv("MAX_TOKENS_ANALYST", "2048"))
MAX_TOKENS_SCRIPTWRITER = int(os.getenv("MAX_TOKENS_SCRIPTWRITER", "3000"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "6000"))

# Retry config for 429 rate-limit errors
_RETRY_DELAYS = [30, 60, 120]  # seconds between attempts (3 total retries)


class Agent:
    def __init__(
        self,
        name: str,
        role: str,
        goal: str,
        backstory: str,
        max_tokens: int = 2048,
        tools: list | None = None,
    ):
        self.name = name
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.max_tokens = max_tokens
        self.tools = tools or []
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    @property
    def system_prompt(self) -> str:
        return (
            f"You are a {self.role}.\n\n"
            f"Goal: {self.goal}\n\n"
            f"Background: {self.backstory}"
        )

    def run(self, user_prompt: str, logger: PipelineLogger) -> tuple[str, dict]:
        """
        Call the LLM and return (text, usage).

        usage = {"input_tokens": int, "output_tokens": int}

        Guardrails applied:
        - user_prompt truncated to MAX_CONTEXT_CHARS before sending
        - response capped at self.max_tokens
        - automatic retry with exponential backoff on 429 RateLimitError
        """
        # -- Input truncation guardrail ---------------------------------------
        if len(user_prompt) > MAX_CONTEXT_CHARS:
            logger.log(
                self.name, "warning",
                f"Prompt truncated from {len(user_prompt)} to {MAX_CONTEXT_CHARS} chars",
            )
            user_prompt = user_prompt[:MAX_CONTEXT_CHARS] + "\n\n[... content truncated by guardrail ...]"

        logger.log(self.name, "start", user_prompt[:200])

        kwargs = dict(
            model=MODEL,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        if self.tools:
            kwargs["tools"] = self.tools

        last_exc = None
        for attempt, retry_delay in enumerate([0] + _RETRY_DELAYS):
            if retry_delay:
                logger.log(
                    self.name, "warning",
                    f"Rate limit hit — waiting {retry_delay}s before retry {attempt}/{len(_RETRY_DELAYS)}",
                )
                time.sleep(retry_delay)
            try:
                response = self._client.messages.create(**kwargs)
                text = "\n".join(
                    block.text for block in response.content if hasattr(block, "text")
                )
                usage = {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
                logger.log(
                    self.name, "complete",
                    f"{len(text)} chars | in={usage['input_tokens']} out={usage['output_tokens']} tokens",
                )
                return text, usage

            except anthropic.RateLimitError as e:
                last_exc = e
                logger.log(self.name, "warning", f"RateLimitError (attempt {attempt + 1}): {e}")
                continue  # will sleep at top of next iteration

            except Exception as e:
                logger.log(self.name, "error", traceback.format_exc())
                raise

        # All retries exhausted
        logger.log(self.name, "error", f"All {len(_RETRY_DELAYS)} retries exhausted: {last_exc}")
        raise last_exc


# ---------------------------------------------------------------------------
# Pre-configured agents
# ---------------------------------------------------------------------------

researcher = Agent(
    name="researcher",
    role="{topic} Senior Researcher",
    goal="Find recent releases, articles, topics, and progress about {topic}",
    backstory=(
        "You're a seasoned researcher with a knack for uncovering the latest "
        "developments in {topic}. Known for your ability to find the most relevant "
        "information and present it in a clear and concise manner. "
        "Always produce a concise, bullet-pointed summary — no more than 500 words."
    ),
    max_tokens=MAX_TOKENS_RESEARCHER,
    tools=[{"type": "web_search_20250305", "name": "web_search"}],
)

reporting_analyst = Agent(
    name="reporting_analyst",
    role="{topic} Reporting Analyst",
    goal="Create detailed reports based on {topic} data analysis and research findings",
    backstory=(
        "You're a meticulous analyst with a keen eye for detail. You're known for "
        "your ability to turn complex data into clear and concise reports, making "
        "it easy for others to understand and act on the information you provide."
    ),
    max_tokens=MAX_TOKENS_ANALYST,
)

scriptwriter = Agent(
    name="scriptwriter",
    role="Scriptwriter",
    goal=(
        "Create a fun, natural, interesting podcast script given a report on {topic}. "
        "Keep it short, under 100 dialogues."
    ),
    backstory=(
        "You're a naturally talented scriptwriter who knows how to take a technical "
        "report and turn it into an engaging, natural, funny & yet in-depth, articulate "
        "podcast between two hosts. You even know where to insert jokes, laughs, and "
        "where and how to bring up technical details."
    ),
    max_tokens=MAX_TOKENS_SCRIPTWRITER,
)
