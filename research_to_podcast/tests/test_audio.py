import os
import unittest
from unittest.mock import MagicMock, call, mock_open, patch

from src.audio import VOICE_POOL, _assign_voices, _parse_script, generate_podcast_audio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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

HOST_FORMAT_SCRIPT = """\
HOST 1: Welcome to today's show about data jobs in 2025.
HOST 2: Thanks for having me, really excited to dig in.
HOST 1: Let's start with salaries — mean is now £50,412.
HOST 2: That's a 5.8% year-on-year increase, which beats wage inflation."""

BOLD_HOST_FORMAT_SCRIPT = """\
**HOST 1:** Welcome to today's show.
**HOST 2:** Great to be here.
**HOST 1 (laughing):** Let's get into it."""

STAGE_DIRECTION_SCRIPT = """\
**Jamie:** *(puts on a deep announcer voice)* Senior ML Engineer. Hedge Fund. Base salary: £160,000.
**Priya:** *(laughing)* That's not a salary, that's a lifestyle."""

NO_MATCH_SCRIPT = """\
# Episode Title

This is an introduction paragraph with no speaker labels.

[OUTRO MUSIC]

---
*Footnote text.*"""


def _make_mock_logger():
    logger = MagicMock()
    logger.log = MagicMock()
    return logger


# ---------------------------------------------------------------------------
# _parse_script
# ---------------------------------------------------------------------------

class TestParseScript(unittest.TestCase):
    """Unit tests for _parse_script — pure function, no I/O."""

    def test_parses_named_speakers(self):
        """Parses **Jamie:** / **Priya:** bold format correctly."""
        lines = _parse_script(NAMED_SPEAKER_SCRIPT)
        speakers = {s for s, _ in lines}
        self.assertIn("Jamie", speakers)
        self.assertIn("Priya", speakers)

    def test_parses_correct_line_count_for_named_speakers(self):
        """Returns one tuple per dialogue line in the named-speaker script."""
        lines = _parse_script(NAMED_SPEAKER_SCRIPT)
        self.assertEqual(len(lines), 10)

    def test_parses_host_format(self):
        """Parses plain HOST 1 / HOST 2 format."""
        lines = _parse_script(HOST_FORMAT_SCRIPT)
        speakers = {s for s, _ in lines}
        self.assertIn("HOST 1", speakers)
        self.assertIn("HOST 2", speakers)
        self.assertEqual(len(lines), 4)

    def test_parses_bold_host_format(self):
        """Parses **HOST 1:** / **HOST 2:** markdown bold format."""
        lines = _parse_script(BOLD_HOST_FORMAT_SCRIPT)
        speakers = {s for s, _ in lines}
        self.assertIn("HOST 1", speakers)
        self.assertIn("HOST 2", speakers)

    def test_handles_inline_stage_directions(self):
        """Strips stage directions from speaker label; preserves dialogue text."""
        lines = _parse_script(STAGE_DIRECTION_SCRIPT)
        self.assertEqual(len(lines), 2)
        jamie_text = next(t for s, t in lines if s == "Jamie")
        self.assertIn("£160,000", jamie_text)

    def test_returns_empty_for_no_matches(self):
        """Returns an empty list when no speaker labels are present."""
        self.assertEqual(_parse_script(NO_MATCH_SCRIPT), [])

    def test_returns_empty_for_empty_string(self):
        """Returns an empty list for an empty input."""
        self.assertEqual(_parse_script(""), [])

    def test_text_does_not_contain_trailing_asterisks(self):
        """Trailing markdown asterisks are stripped from dialogue text."""
        lines = _parse_script(NAMED_SPEAKER_SCRIPT)
        for _, text in lines:
            self.assertFalse(text.endswith("*"), f"Trailing * found in: {text!r}")

    def test_preserves_salary_figures_in_text(self):
        """Salary figures like £50,412 survive intact in the parsed text."""
        lines = _parse_script(NAMED_SPEAKER_SCRIPT)
        texts = [t for _, t in lines]
        self.assertTrue(any("£50,412" in t for t in texts))


# ---------------------------------------------------------------------------
# _assign_voices
# ---------------------------------------------------------------------------

class TestAssignVoices(unittest.TestCase):
    """Unit tests for _assign_voices."""

    def test_assigns_first_speaker_to_first_voice(self):
        lines = [("Jamie", "Hello"), ("Priya", "Hi"), ("Jamie", "How are you?")]
        voice_map = _assign_voices(lines)
        self.assertEqual(voice_map["Jamie"], VOICE_POOL[0])

    def test_assigns_second_speaker_to_second_voice(self):
        lines = [("Jamie", "Hello"), ("Priya", "Hi")]
        voice_map = _assign_voices(lines)
        self.assertEqual(voice_map["Priya"], VOICE_POOL[1])

    def test_assigns_host_labels(self):
        lines = [("HOST 1", "Hello"), ("HOST 2", "Hi")]
        voice_map = _assign_voices(lines)
        self.assertEqual(voice_map["HOST 1"], VOICE_POOL[0])
        self.assertEqual(voice_map["HOST 2"], VOICE_POOL[1])

    def test_caps_at_two_voices(self):
        """Only the first two speakers get voice assignments."""
        lines = [("A", "1"), ("B", "2"), ("C", "3")]
        voice_map = _assign_voices(lines)
        self.assertNotIn("C", voice_map)

    def test_single_speaker_gets_first_voice(self):
        lines = [("Jamie", "Monologue line.")]
        voice_map = _assign_voices(lines)
        self.assertEqual(voice_map["Jamie"], VOICE_POOL[0])


# ---------------------------------------------------------------------------
# generate_podcast_audio
# ---------------------------------------------------------------------------

class TestGeneratePodcastAudio(unittest.TestCase):
    """Unit tests for generate_podcast_audio with TTS and pydub mocked out."""

    def _run(self, script: str, logger=None):
        """Helper: run generate_podcast_audio with all I/O mocked."""
        fake_audio = MagicMock()
        fake_audio.__add__ = lambda s, o: fake_audio
        fake_audio.__radd__ = lambda s, o: fake_audio
        fake_audio.__len__ = lambda s: 30000  # 30 seconds

        with patch("src.audio.asyncio.run", return_value=b"fake_mp3_bytes"), \
             patch("src.audio.AudioSegment.from_mp3", return_value=fake_audio), \
             patch("src.audio.AudioSegment.silent", return_value=fake_audio), \
             patch("src.audio.AudioSegment.empty", return_value=fake_audio), \
             patch("src.audio.os.makedirs"), \
             patch("src.audio.os.unlink"), \
             patch("builtins.open", mock_open()):
            fake_audio.export = MagicMock()
            return generate_podcast_audio(script, logger or _make_mock_logger())

    def test_returns_mp3_path(self):
        """generate_podcast_audio() returns a path ending in .mp3."""
        path = self._run(NAMED_SPEAKER_SCRIPT)
        self.assertTrue(path.endswith(".mp3"))

    def test_mp3_path_is_in_output_dir(self):
        """Output file is placed under output/."""
        path = self._run(NAMED_SPEAKER_SCRIPT)
        self.assertTrue(path.startswith("output/"))

    def test_raises_on_empty_script(self):
        """Raises ValueError when the script contains no parseable speaker lines."""
        with patch("src.audio.os.makedirs"):
            with self.assertRaises(ValueError):
                generate_podcast_audio(NO_MATCH_SCRIPT, _make_mock_logger())

    def test_synthesises_each_dialogue_line(self):
        """asyncio.run is called once per parsed dialogue line."""
        lines = _parse_script(NAMED_SPEAKER_SCRIPT)
        with patch("src.audio.asyncio.run", return_value=b"fake") as mock_run, \
             patch("src.audio.AudioSegment.from_mp3", return_value=MagicMock()), \
             patch("src.audio.AudioSegment.silent", return_value=MagicMock()), \
             patch("src.audio.AudioSegment.empty", return_value=MagicMock()), \
             patch("src.audio.os.makedirs"), \
             patch("src.audio.os.unlink"), \
             patch("builtins.open", mock_open()):
            generate_podcast_audio(NAMED_SPEAKER_SCRIPT, _make_mock_logger())

        self.assertEqual(mock_run.call_count, len(lines))

    def test_logs_parsed_line_count(self):
        """Logger records the number of parsed dialogue lines."""
        mock_logger = _make_mock_logger()
        self._run(NAMED_SPEAKER_SCRIPT, mock_logger)

        log_messages = [str(c) for c in mock_logger.log.call_args_list]
        self.assertTrue(
            any("Parsed" in m and "dialogue lines" in m for m in log_messages)
        )

    def test_logs_voice_assignments(self):
        """Logger records which voice was assigned to each speaker."""
        mock_logger = _make_mock_logger()
        self._run(NAMED_SPEAKER_SCRIPT, mock_logger)

        log_messages = [str(c) for c in mock_logger.log.call_args_list]
        self.assertTrue(any("Voice assignments" in m for m in log_messages))

    def test_works_with_host_format_script(self):
        """generate_podcast_audio() works with HOST 1 / HOST 2 format too."""
        path = self._run(HOST_FORMAT_SCRIPT)
        self.assertTrue(path.endswith(".mp3"))


if __name__ == "__main__":
    unittest.main()
