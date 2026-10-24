"""Fork: youtube-ingest files one checked ingest transaction per video (no network)."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "youtube-ingest.py"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("youtube_ingest", SCRIPT)
yi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yi)
from claude_obsidian import conventions as cv  # noqa: E402

DASH = "\u2014"
CONVENTIONS = {
    "schema": cv.CONVENTIONS_SCHEMA,
    "log": {"style": "dated-bullets"},
    "frontmatter": {"values": {"status": ["developing", "evergreen"]}, "list_properties": ["tags"]},
    "text": {"forbidden_characters": [DASH, "\u2013"]},
    "catalogs": {"folder_index": "_index.md"},
}
SUMMARY = f"## TL;DR\nA drill test {DASH} two drills compared.\n\n## Key Points\n- 1,450 rpm\n\n## Table\nN/A\n\n## Remarks\nN/A"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.vault = Path(self.temporary.name) / "vault"
        for folder in ("wiki/sources", ".obsidian", ".raw", ".vault-meta"):
            (self.vault / folder).mkdir(parents=True)
        (self.vault / "wiki/log.md").write_text("# Wiki Log\n\n## 2026-10-23\n\n- **save** | Older\n", encoding="utf-8")
        (self.vault / "wiki/sources/_index.md").write_text("# Sources\n\n## Tools\n\n- [[Other]]\n", encoding="utf-8")
        (self.vault / cv.CONVENTIONS_PATH).write_text(json.dumps(CONVENTIONS), encoding="utf-8")
        self.conventions = yi.load_conventions(self.vault)

    def bundle(self, **overrides):
        values = dict(vid="zlv4TAi9Ob4", title=f"Can Makita beat DeWalt? {DASH} test", author="Kaleb Ross",
                      lang="en", transcript="raw words", name="Makita vs DeWalt Drill Test (Kaleb Ross)",
                      summary=SUMMARY, summarized_by=yi.HAIKU, today="2026-10-24",
                      conventions=self.conventions, existing=None)
        values.update(overrides)
        return yi.build_bundle(self.vault, **values)


class PureTests(unittest.TestCase):
    def test_ids_names_and_replies(self):
        for url in ("https://www.youtube.com/watch?v=zlv4TAi9Ob4&t=3", "https://youtu.be/zlv4TAi9Ob4",
                    "https://www.youtube.com/shorts/zlv4TAi9Ob4", "zlv4TAi9Ob4"):
            self.assertEqual("zlv4TAi9Ob4", yi.extract_id(url))
        with self.assertRaises(ValueError):
            yi.extract_id("https://example.com/x")
        self.assertEqual("A - B C (x)", yi.safe_name(f"A {DASH} B: C? (x)"))
        self.assertEqual("Note", yi.safe_name("Note.md"))
        name, summary = yi.parse_reply("FILENAME: \"Drill Test (Kaleb Ross)\"\n\n" + SUMMARY, "Kaleb Ross")
        self.assertEqual("Drill Test (Kaleb Ross)", name)
        self.assertTrue(summary.startswith("## TL;DR"))
        with self.assertRaises(ValueError):
            yi.parse_reply(SUMMARY, "x")
        with self.assertRaises(ValueError):
            yi.parse_reply("FILENAME: x\n\n## TL;DR\nonly", "x")

    def test_catalog_lines_go_under_uncatalogued(self):
        text = yi.add_to_catalog("# S\n\n## Tools\n\n- [[A]]\n", "B")
        self.assertTrue(text.endswith("## Uncatalogued\n\nPages filed before catalogs were checked. "
                                      "Move each one under its topic heading.\n\n- [[B]]\n"))
        again = yi.add_to_catalog(text, "C")
        self.assertTrue(again.endswith("- [[B]]\n- [[C]]\n"))
        self.assertEqual(again, yi.add_to_catalog(again, "C"))

    def test_haiku_needs_egress_consent(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(ROOT), "zlv4TAi9Ob4"],
                                capture_output=True, text=True)
        self.assertEqual(2, result.returncode)


class BundleTests(Fixture):
    def test_one_checked_transaction_files_everything(self):
        bundle, note_path = self.bundle()
        result = yi.apply(self.vault, bundle)
        self.assertEqual("wiki/sources/Makita vs DeWalt Drill Test (Kaleb Ross).md", note_path)
        self.assertIn(note_path, result["changed_paths"])
        note = (self.vault / note_path).read_text(encoding="utf-8")
        self.assertIn("A drill test - two drills compared.", note)
        self.assertIn("enrichment: pending", note)
        self.assertRegex(note, r"(?m)^address: c-\d{6}$")
        raw = self.vault / ".raw/youtube/Makita vs DeWalt Drill Test (Kaleb Ross).md"
        self.assertIn("youtube_id: zlv4TAi9Ob4", raw.read_text(encoding="utf-8"))
        catalog = (self.vault / "wiki/sources/_index.md").read_text(encoding="utf-8")
        self.assertIn("## Uncatalogued", catalog)
        self.assertIn("- [[Makita vs DeWalt Drill Test (Kaleb Ross)]]", catalog)
        log = (self.vault / "wiki/log.md").read_text(encoding="utf-8")
        self.assertIn("## 2026-10-24\n\n- **youtube** | Can Makita beat DeWalt? - test\n", log)
        manifest = json.loads((self.vault / ".raw/.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("pending", manifest["sources"][".raw/youtube/" + raw.name]["enrichment"])
        self.assertEqual(note_path, yi.already_filed(self.vault, "zlv4TAi9Ob4"))

    def test_a_name_owned_by_another_video_is_never_overwritten(self):
        taken = self.vault / "wiki/sources/Makita vs DeWalt Drill Test (Kaleb Ross).md"
        taken.write_text("---\nyoutube_id: OTHERVIDEO1\n---\n", encoding="utf-8")
        _, note_path = self.bundle()
        self.assertEqual("wiki/sources/Makita vs DeWalt Drill Test (Kaleb Ross) (zlv4TAi9Ob4).md", note_path)

    def test_force_refreshes_the_same_note_and_keeps_the_transcript(self):
        yi.apply(self.vault, self.bundle()[0])
        existing = yi.already_filed(self.vault, "zlv4TAi9Ob4")
        bundle, note_path = self.bundle(name="A Different Name", summary=SUMMARY.replace("1,450", "1,500"),
                                        existing=existing)
        self.assertEqual(existing, note_path)
        self.assertEqual([], bundle["address_requests"])
        self.assertNotIn(".raw/youtube/A Different Name.md", [w["path"] for w in bundle["writes"]])
        address = next(line for line in (self.vault / note_path).read_text(encoding="utf-8").splitlines()
                       if line.startswith("address:"))
        yi.apply(self.vault, bundle)
        refreshed = (self.vault / note_path).read_text(encoding="utf-8")
        self.assertIn("1,500 rpm", refreshed)
        self.assertIn(address, refreshed.splitlines())


if __name__ == "__main__":
    unittest.main()
