"""Offline tests for the podcast command parser and script checks."""
import tempfile
import unittest
from pathlib import Path

from src.podcast import parse_command, read_note
from src.podcast_script import apply_fixes, bad_lines, parse_jsonl


class TestParseCommand(unittest.TestCase):
    def test_not_podcast(self):
        self.assertIsNone(parse_command("nice paper"))
        self.assertIsNone(parse_command("podcasting is fun"))

    def test_defaults(self):
        o = parse_command("podcast")
        self.assertEqual((o["minutes"], o["format"], o["focus"]), (25, "deep_dive", ""))

    def test_options_and_focus(self):
        o = parse_command("Podcast long critique focus on the stats")
        self.assertEqual((o["minutes"], o["format"], o["focus"]), (40, "critique", "focus on the stats"))
        o = parse_command("podcast 15 min: sex differences")
        self.assertEqual((o["minutes"], o["focus"]), (15, "sex differences"))


class TestScriptChecks(unittest.TestCase):
    def test_parse_jsonl_skips_noise(self):
        out = parse_jsonl('here you go\n{"speaker": "A", "text": "Hi."}\n```\n{"segment": true}')
        self.assertEqual(len(out), 2)

    def test_bad_lines_and_fix(self):
        s = [{"speaker": "A", "text": "About forty percent."},
             {"speaker": "B", "text": "So 40% then?"},
             {"speaker": "A", "text": "x " * 110}]
        self.assertEqual(bad_lines(s), [1, 2])
        fixed = apply_fixes(s, [{"i": 1, "text": "So forty percent then?"},
                                {"i": 2, "text": "First half. | Second half."}])
        self.assertEqual(len(fixed), 4)
        self.assertEqual(fixed[3], {"speaker": "A", "text": "Second half."})
        self.assertEqual(bad_lines(fixed), [])


class TestReadNote(unittest.TestCase):
    def test_reads_fields_and_fulltext(self):
        note = ("---\ncitekey: Doe_2024\nyear: 2024\ndoi: 10.1/x\n---\n"
                "> **Authors**:: Doe, J; Roe, R\n> **Title**:: A study\n"
                "# Full text\n<details>\n<summary>Full text (Markdown)</summary>\n\nBody text.\n\n</details>\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "@Doe_2024.md"
            p.write_text(note)
            r = read_note(p)
        self.assertEqual((r["citekey"], r["doi"], r["title"], r["fulltext"]),
                         ("Doe_2024", "10.1/x", "A study", "Body text."))


if __name__ == "__main__":
    unittest.main()
