import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "persist_model_cache",
    Path(__file__).resolve().parents[1] / "kaggle" / "persist_model_cache.py",
)
CACHE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CACHE)


class CachePersistenceTests(unittest.TestCase):
    def test_pack_verify_restore_roundtrip_with_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            archive = root / "cache.tar.gz"
            restored = root / "restored"
            for required in CACHE.REQUIRED:
                folder = cache / required / "snapshot"
                folder.mkdir(parents=True)
                (folder / "weights.bin").write_bytes(b"manga-model-test-bytes")
            CACHE.pack(cache, archive)
            CACHE.verify_archive(archive)
            CACHE.restore(archive, restored)
            CACHE.validate_cache(restored)
            for required in CACHE.REQUIRED:
                self.assertEqual(
                    (restored / required / "snapshot" / "weights.bin").read_bytes(),
                    b"manga-model-test-bytes",
                )

    def test_incomplete_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            (cache / CACHE.REQUIRED[0] / "snapshot").mkdir(parents=True)
            with self.assertRaises(SystemExit):
                CACHE.validate_cache(cache)


if __name__ == "__main__":
    unittest.main()