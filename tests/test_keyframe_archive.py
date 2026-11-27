import tempfile
import unittest
from pathlib import Path

from server import archive_and_replace_keyframe


class KeyframeArchiveTests(unittest.TestCase):
    def test_replacement_archives_previous_frame_and_keeps_new_frame(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "keyframes" / "shot_0003.png"
            source = root / "worker" / "shot_0003.png"
            target.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            target.write_bytes(b"old-frame")
            source.write_bytes(b"new-frame")

            archived = archive_and_replace_keyframe(source, target)

            self.assertIsNotNone(archived)
            self.assertTrue(archived.is_file())
            self.assertEqual(archived.read_bytes(), b"old-frame")
            self.assertEqual(target.read_bytes(), b"new-frame")
            self.assertFalse(target.with_name("shot_0003.incoming.png").exists())

    def test_first_frame_has_no_fake_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "keyframes" / "shot_0001.png"
            source = root / "worker.png"
            source.write_bytes(b"first-frame")
            self.assertIsNone(archive_and_replace_keyframe(source, target))
            self.assertEqual(target.read_bytes(), b"first-frame")


if __name__ == "__main__":
    unittest.main()
