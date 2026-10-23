"""Fork: the one log format, read leniently and written strictly."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_obsidian import logfmt  # noqa: E402

LOG = """\
---
type: meta
title: "Wiki Log"
---

# Wiki Log

Chronological record of wiki operations.

## 2026-10-22

- **save** | Older save
  - Created [[Older]].

## 2026-10-21

- **ingest** | Oldest ingest
"""

DASH = "\u2014"


class FormatTests(unittest.TestCase):
    def test_format_entry_renders_op_title_and_details(self):
        self.assertEqual(
            "- **save** | Title here\n  - [[Page]]: created.\n  - Hot updated.\n",
            logfmt.format_entry("save", " Title here ", ["[[Page]]: created.", "Hot updated."]),
        )
        self.assertEqual("- **capture + save** | T\n", logfmt.format_entry("capture  +  save", "T"))

    def test_format_entry_refuses_the_legacy_shapes(self):
        for op in ("query→web", "Query", "query (standard)", "chat ingest", "save2", ""):
            with self.subTest(op=op):
                with self.assertRaises(logfmt.LogFormatError):
                    logfmt.format_entry(op, "Title")
        with self.assertRaises(logfmt.LogFormatError):
            logfmt.format_entry("save", "two\nlines")
        with self.assertRaises(logfmt.LogFormatError):
            logfmt.format_entry("save", "Title", ["ok", ""])


class InsertTests(unittest.TestCase):
    def test_same_day_goes_on_top_of_that_day(self):
        block = logfmt.format_entry("query", "Newest", ["Answered."])
        updated = logfmt.insert_entry(LOG.replace("2026-10-22", "2026-10-23"), "2026-10-23", block)
        self.assertIn("## 2026-10-23\n\n- **query** | Newest\n  - Answered.\n\n- **save** | Older save", updated)

    def test_new_day_gets_its_own_heading_above_the_newest(self):
        updated = logfmt.insert_entry(LOG, "2026-10-23", logfmt.format_entry("save", "New"))
        self.assertIn("record of wiki operations.\n\n## 2026-10-23\n\n- **save** | New\n\n## 2026-10-22\n", updated)

    def test_every_existing_line_keeps_its_order(self):
        updated = logfmt.insert_entry(LOG, "2026-10-23", logfmt.format_entry("save", "New", ["d"]))
        remaining = iter(updated.split("\n"))
        self.assertTrue(all(any(line == got for got in remaining) for line in LOG.split("\n")))
        self.assertTrue(logfmt.is_prepend_of(LOG, updated))

    def test_empty_log_gets_its_first_heading(self):
        head = "# Wiki Log\n\nNewest first.\n"
        updated = logfmt.insert_entry(head, "2026-10-23", logfmt.format_entry("save", "First"))
        self.assertEqual(head + "\n## 2026-10-23\n\n- **save** | First\n", updated)
        self.assertTrue(logfmt.is_prepend_of(head, updated))

    def test_a_heading_less_log_keeps_every_byte(self):
        for head in ("", "\n", "# Log", "# Log\n", "# Log\n\n"):
            with self.subTest(head=head):
                updated = logfmt.insert_entry(head, "2026-10-23", logfmt.format_entry("save", "First"))
                self.assertTrue(updated.startswith(head))
                self.assertTrue(updated.endswith("## 2026-10-23\n\n- **save** | First\n"))
                self.assertNotIn("\n\n\n", updated)
                self.assertTrue(logfmt.is_prepend_of(head, updated))

    def test_a_day_older_than_the_newest_is_refused(self):
        with self.assertRaises(logfmt.LogFormatError):
            logfmt.insert_entry(LOG, "2026-10-01", logfmt.format_entry("save", "Late"))

    def test_block_must_hold_one_entry(self):
        two = "- **save** | A\n- **save** | B\n"
        with self.assertRaises(logfmt.LogFormatError):
            logfmt.insert_entry(LOG, "2026-10-23", two)

    def test_prepend_check_rejects_edits_to_history(self):
        updated = logfmt.insert_entry(LOG, "2026-10-23", logfmt.format_entry("save", "New"))
        self.assertFalse(logfmt.is_prepend_of(LOG, updated.replace("Older save", "Rewritten")))
        self.assertFalse(logfmt.is_prepend_of(LOG, LOG))


class ReadTests(unittest.TestCase):
    def test_strict_reading_matches_only_the_format(self):
        self.assertEqual(("save", "Title"), logfmt.match_entry("- **save** | Title"))
        self.assertEqual(("capture + save", "T"), logfmt.match_entry("- **capture + save** | T"))
        self.assertIsNone(logfmt.match_entry(f"- **save** {DASH} filed [[X]]"))

    def test_lenient_reading_keeps_each_legacy_label(self):
        cases = {
            "- **query→web** | Soulmask: FPV?": ("query→web", "Soulmask: FPV?"),
            f"- **youtube** {DASH} ingested transcript": ("youtube", "ingested transcript"),
            "- **query** (standard) | \"EV3 vs Ioniq 3\"": ("query (standard)", "\"EV3 vs Ioniq 3\""),
            "- **ingest** (batch, 3 sources) | Spain flat": ("ingest (batch, 3 sources)", "Spain flat"),
            "- **correction (user)** | 14PHS has no balcony": ("correction (user)", "14PHS has no balcony"),
            "- **chat ingest** | WhatsApp exchange": ("chat ingest", "WhatsApp exchange"),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertIsNone(logfmt.match_entry(line))
                self.assertEqual(expected, logfmt.match_entry(line, lenient=True))

    def test_bold_prose_without_a_separator_is_not_an_entry(self):
        self.assertIsNone(logfmt.match_entry("- **Note** this is prose", lenient=True))
        self.assertIsNone(logfmt.match_entry("  - **save** | indented", lenient=True))

    def test_dated_heading_with_a_suffix_still_names_its_day(self):
        self.assertIsNone(logfmt.match_date_heading("## 2026-09-06 (later)"))
        self.assertEqual("2026-09-06", logfmt.match_date_heading("## 2026-09-06 (later)", lenient=True))


class ViolationTests(unittest.TestCase):
    def test_history_and_preamble_are_not_judged(self):
        legacy = LOG.replace("- **ingest** | Oldest ingest", f"- **ingest** {DASH} oldest")
        self.assertEqual([], logfmt.added_line_violations(legacy, legacy))
        updated = logfmt.insert_entry(legacy, "2026-10-23", logfmt.format_entry("save", "New"))
        self.assertEqual([], logfmt.added_line_violations(legacy, updated.replace(
            "Chronological record", "Free preamble text. Chronological record")))

    def test_new_lines_off_the_format_are_reported(self):
        bad = LOG.replace("## 2026-10-22\n", "## 2026-10-23 (late)\n\n- **query→web** | X\nloose text\n\n## 2026-10-22\n")
        found = logfmt.added_line_violations(LOG, bad)
        self.assertEqual(3, len(found), found)
        self.assertTrue(all(isinstance(number, int) for number, _ in found))

    def test_an_impossible_date_heading_is_reported(self):
        bad = LOG.replace("## 2026-10-22\n", "## 2026-02-30\n\n- **save** | X\n\n## 2026-10-22\n")
        self.assertEqual(1, len(logfmt.added_line_violations(LOG, bad)))

    def test_moved_lines_and_detail_lines_pass(self):
        updated = logfmt.insert_entry(LOG, "2026-10-23", logfmt.format_entry("fold", "Rollup", ["Range: x"]))
        self.assertEqual([], logfmt.added_line_violations(LOG, updated))

    def test_declared_ops_are_enforced(self):
        updated = logfmt.insert_entry(LOG, "2026-10-23", logfmt.format_entry("capture + save", "X"))
        found = logfmt.added_line_violations(LOG, updated, ops=frozenset({"save"}))
        self.assertEqual(1, len(found))
        self.assertIn("capture", found[0][1])


if __name__ == "__main__":
    unittest.main()
