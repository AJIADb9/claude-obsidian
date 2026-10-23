"""Fork: parallel prefix rebuild builds in shards and installs under one lock."""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prefix-parallel.py"
PREFIX = ROOT / "scripts" / "contextual-prefix.py"
BM25 = ROOT / "scripts" / "bm25-index.py"


def run(*command):
    return subprocess.run([sys.executable, *map(str, command)], capture_output=True, text=True,
                          encoding="utf-8", timeout=120)


class PrefixParallelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.vault = base / "vault"
        self.work = base / "work"
        (self.vault / "wiki/notes").mkdir(parents=True)
        (self.vault / ".obsidian").mkdir()
        for number in range(7):
            body = "\n\n".join(f"Paragraph {i} of note {number} about topic {number}." for i in range(3 + number))
            (self.vault / f"wiki/notes/Note {number}.md").write_text(
                f"---\ntitle: Note {number}\n---\n\n# Note {number}\n\n{body}\n", encoding="utf-8")

    def records(self, vault):
        records = {}
        for path in (vault / ".vault-meta/chunks").rglob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            record.pop("created_at", None)
            records[path.relative_to(vault / ".vault-meta/chunks").as_posix()] = record
        return records

    def test_provider_run_needs_egress_consent(self):
        result = run(SCRIPT, "--vault", self.vault)
        self.assertEqual(2, result.returncode)
        self.assertIn("--allow-egress", result.stderr)

    def test_shards_match_a_serial_build_and_keep_the_previous_store(self):
        reference = Path(self.temporary.name) / "reference"
        subprocess.run(["cp", "-r", str(self.vault), str(reference)], check=True)
        self.assertEqual(0, run(PREFIX, "--vault", reference, "--all", "--no-llm").returncode)
        self.assertEqual(0, run(PREFIX, "--vault", self.vault, "--all", "--no-llm").returncode)
        self.assertEqual(0, run(BM25, "--vault", self.vault, "build").returncode)

        result = run(SCRIPT, "--vault", self.vault, "--no-llm", "--shards", "3", "--work-dir", self.work)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(self.records(reference), self.records(self.vault))
        meta = self.vault / ".vault-meta"
        self.assertTrue((meta / "bm25/index.json").is_file())
        self.assertEqual(1, len(list(meta.glob("chunks.prev-*"))))
        self.assertEqual(1, len(list(meta.glob("bm25.prev-*"))))
        self.assertFalse((meta / "mutation.lock").exists())

    def test_pages_edited_during_the_run_are_resynced_under_the_lock(self):
        result = run(SCRIPT, "--vault", self.vault, "--no-llm", "--shards", "2", "--work-dir", self.work,
                     "--no-install")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        work = next(self.work.glob("prefix-parallel.*"))
        self.assertEqual([], list(work.glob("shard*/")))
        (self.vault / "wiki/notes/Note 1.md").write_text(
            "---\ntitle: Note 1\n---\n\n# Note 1\n\nEdited while the build ran.\n", encoding="utf-8")
        (self.vault / "wiki/notes/Note 2.md").unlink()
        backups = Path(self.temporary.name) / "backups"
        result = run(SCRIPT, "--vault", self.vault, "--no-llm", "--install-only", work, "--backup-dir", backups)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("1 changed or new, 1 removed", result.stdout)
        records = self.records(self.vault).values()
        self.assertTrue(any("Edited while the build ran." in record["raw_text"] for record in records))
        self.assertFalse(any(record["page_path"] == "wiki/notes/Note 2.md" for record in records))
        dated = backups / time.strftime("%Y-%m-%d")
        self.assertEqual([], list(dated.glob("chunks.*")))  # there was no previous store to keep
        self.assertTrue((self.vault / ".vault-meta/bm25/index.json").is_file())


if __name__ == "__main__":
    unittest.main()
