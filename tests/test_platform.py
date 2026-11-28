"""Offline tests for the cross-platform and setup helpers.

Run: uv run python -m unittest tests.test_platform -v
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config
from src.setup import set_yaml
from src.slack_listener import safe_filename


class TestClaudeBin(unittest.TestCase):
    def test_configured_path_wins(self):
        with mock.patch.dict(config.CONFIG, {"node": {"claude_bin": "/x/claude"}}):
            self.assertEqual(config.claude_bin(), "/x/claude")

    def test_found_on_path(self):
        with mock.patch.dict(config.CONFIG, {"node": {"claude_bin": ""}}), \
             mock.patch("shutil.which", return_value="C:/bin/claude.cmd"):
            self.assertEqual(config.claude_bin(), "C:/bin/claude.cmd")


class TestSafeFilename(unittest.TestCase):
    def test_windows_illegal_chars(self):
        self.assertEqual(safe_filename('F1_a:b?c*"d".pdf'), "F1_a_b_c__d_.pdf")
        self.assertEqual(safe_filename("..."), "file")


class TestSetYaml(unittest.TestCase):
    TEXT = 'vault:\n  path: ""   # folder\npodcast:\n  model: x\n  spotify:\n    enabled: false\n'

    def test_nested_and_comment_kept(self):
        t = set_yaml(self.TEXT, ["vault", "path"], '"/v"')
        self.assertIn('  path: "/v"   # folder', t)
        t = set_yaml(t, ["podcast", "spotify", "enabled"], "true")
        self.assertIn("    enabled: true", t)

    def test_missing_leaf_is_added(self):
        t = set_yaml(self.TEXT, ["podcast", "enabled"], "true")
        self.assertIn("podcast:\n  enabled: true\n  model: x", t)


class TestHeic(unittest.TestCase):
    def test_heic_to_jpg(self):
        from PIL import Image
        from pillow_heif import register_heif_opener

        from src.process_poster import _convert_if_heic

        register_heif_opener()
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "poster.heic"
            Image.new("RGB", (64, 48), "white").save(src, format="HEIF")
            out = _convert_if_heic(src)
            self.assertEqual(out.suffix, ".jpg")
            self.assertEqual(Image.open(out).size, (64, 48))


class TestKokoroBackend(unittest.TestCase):
    def test_backend_choice(self):
        from src import podcast_audio

        with mock.patch("sys.platform", "darwin"), mock.patch("platform.machine", return_value="arm64"):
            self.assertTrue(podcast_audio.use_mlx())
        with mock.patch("sys.platform", "linux"), mock.patch("platform.machine", return_value="x86_64"):
            self.assertFalse(podcast_audio.use_mlx())


if __name__ == "__main__":
    unittest.main()
