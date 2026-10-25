from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ai_log_store import AIAdviceStore


class TestAIAdviceStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = AIAdviceStore(self.base)

    def test_create_edit_and_audit(self):
        self.store.create("2026-10-22/review.md", "first")
        self.assertEqual("first", self.store.read("2026-10-22/review.md"))
        self.store.revise("2026-10-22/review.md", "second")
        self.assertEqual("second", self.store.read("2026-10-22/review.md"))
        audit = [json.loads(line) for line in (self.base / "log" / "ai_file_edits.jsonl").read_text().splitlines()]
        self.assertEqual(["ai.file_created", "ai.file_revised"], [a["event_type"] for a in audit])
        self.assertEqual(1, len(list((self.base / "ai log" / "versions").glob("*"))))
        self.assertEqual("first", next((self.base / "ai log" / "versions").glob("*")).read_text())

    def test_no_overwrite_on_create(self):
        self.store.create("2026-10-22/one.md", "one")
        with self.assertRaises(FileExistsError):
            self.store.create("2026-10-22/one.md", "two")

    def test_reject_traversal(self):
        bad_paths = [
            "../settings/secrets.json", "../../x.md", "2026-10-22/../../foo.md",
            "/tmp/z.md", "C:\\users\\a.md", "2026-10-22/data.exe",
            "2026-10-22/foo/other.md", "2026-10-22/../evil.md"
        ]
        for bad in bad_paths:
            with self.subTest(path=bad), self.assertRaises(ValueError):
                self.store.create(bad, "x")

    def test_reject_link(self):
        outer = self.base / "outer"
        outer.mkdir()
        link = self.base / "ai log" / "2026-10-22"
        try:
            link.symlink_to(outer, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable in current runtime")
        with self.assertRaises(ValueError):
            self.store.create("2026-10-22/abc.md", "x")

    def test_size_cap(self):
        with self.assertRaises(ValueError):
            self.store.create("2026-10-22/huge.md", "x" * 600_000)


if __name__ == "__main__":
    unittest.main()
