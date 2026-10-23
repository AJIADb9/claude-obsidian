"""Fork: vault-declared conventions, judged on what a write adds."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_obsidian import conventions as cv  # noqa: E402
from claude_obsidian.lint_engine import lint_vault  # noqa: E402
from claude_obsidian.transaction import (  # noqa: E402
    BUNDLE_SCHEMA, TransactionValidationError, apply_bundle, inspect_bundle,
)
from claude_obsidian.workflow import inspect_workflow  # noqa: E402

CORE = ROOT / "scripts" / "claude-obsidian.py"
REPAIR = ROOT / "scripts" / "conventions-repair.py"
DASH = "\u2014"
RANGE = "\u2013"

CONVENTIONS = {
    "schema": cv.CONVENTIONS_SCHEMA,
    "log": {"style": "dated-bullets"},
    "frontmatter": {
        "values": {"status": ["seed", "developing", "evergreen"], "confidence": ["low", "medium", "high"]},
        "value_aliases": {"status": {"stable": "evergreen"}, "confidence": {"medium-high": "medium"}},
        "list_properties": ["tags"],
        "property_aliases": {"retrieved": "date_fetched"},
    },
    "text": {"forbidden_characters": [DASH, RANGE], "exempt": ["wiki/folds/*"]},
    "catalogs": {"folder_index": "_index.md"},
}

LOG = """\
# Wiki Log

## 2026-10-22

- **save** | Older
"""


def page(status="developing", body="Body text.\n", extra=""):
    return f"---\ntype: concept\nstatus: {status}\ntags:\n  - x\n{extra}---\n\n# Title\n\n{body}"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.vault = Path(self.temporary.name) / "vault"
        for folder in ("wiki/concepts", ".obsidian", ".raw", ".vault-meta"):
            (self.vault / folder).mkdir(parents=True)
        self.write("wiki/log.md", LOG)
        self.write("wiki/concepts/_index.md", "# Concepts\n\n- [[Existing]]\n")
        self.write("wiki/concepts/Existing.md", page(status="stable", body=f"Old {DASH} line.\n"))
        self.conventions = cv.parse_conventions(CONVENTIONS)

    def write(self, relative, text):
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))

    def declare(self, payload=None):
        self.write(cv.CONVENTIONS_PATH, json.dumps(payload or CONVENTIONS))

    def bundle(self, writes, operation_id="op-conventions", operation_type="generic"):
        expected = {}
        for write in writes:
            target = self.vault / write["path"]
            expected[write["path"]] = sha(target.read_bytes().decode("utf-8")) if target.exists() else None
            write.setdefault("mode", "replace" if target.exists() else "create")
        return {"schema": BUNDLE_SCHEMA, "operation_id": operation_id, "operation_type": operation_type,
                "expected_hashes": expected, "writes": writes}

    def assert_refused(self, bundle, fragment):
        with self.assertRaises(TransactionValidationError) as caught:
            inspect_bundle(self.vault, bundle)
        self.assertEqual("CONVENTION_VIOLATION", caught.exception.code)
        self.assertIn(fragment, str(caught.exception))


class ParseTests(unittest.TestCase):
    def test_bad_documents_are_refused(self):
        for broken in (
            {**CONVENTIONS, "schema": "other"},
            {**CONVENTIONS, "extra": {}},
            {**CONVENTIONS, "log": {"style": "headings"}},
            {**CONVENTIONS, "frontmatter": {"values": {"status": ["a"]}, "value_aliases": {"status": {"b": "c"}}}},
            {**CONVENTIONS, "frontmatter": {"property_aliases": {"a": "b", "b": "c"}}},
            {**CONVENTIONS, "text": {"forbidden_characters": ["ab"]}},
            {**CONVENTIONS, "catalogs": {"folder_index": "x/_index.md"}},
        ):
            with self.subTest(broken=broken):
                with self.assertRaises(ValueError):
                    cv.parse_conventions(broken)

    def test_every_section_is_optional(self):
        empty = cv.parse_conventions({"schema": cv.CONVENTIONS_SCHEMA})
        self.assertEqual([], cv.check_writes([("wiki/a.md", page(status="any").encode(), None)], empty,
                                             lambda _: None))


class RuleTests(Fixture):
    def check(self, writes):
        return cv.check_writes([(path, new.encode(), old.encode() if old is not None else None)
                                for path, new, old in writes],
                               self.conventions, lambda relative: None)

    def test_new_frontmatter_violations_are_reported_with_the_alias(self):
        found = self.check([("wiki/concepts/A.md", page(status="stable", extra="retrieved: 2026-10-01\n"), None)])
        messages = " ".join(item.message for item in found)
        self.assertIn("use 'evergreen'", messages)
        self.assertIn("'date_fetched'", messages)

    def test_inline_tags_are_reported(self):
        text = "---\nstatus: seed\ntags: [a, b]\n---\n"
        self.assertEqual({("tags", "inline-list", "[a, b]")}, cv.frontmatter_violations(text, self.conventions))
        self.assertEqual(set(), cv.frontmatter_violations("---\nstatus: seed\ntags: []\n---\n", self.conventions))

    def test_crlf_frontmatter_is_read(self):
        text = "---\r\nstatus: stable\r\n---\r\n"
        self.assertEqual({("status", "value", "stable")}, cv.frontmatter_violations(text, self.conventions))

    def test_old_violations_do_not_block_an_unrelated_edit(self):
        old = page(status="stable", body=f"Old {DASH} line.\n")
        new = old + "New plain line.\n"
        self.assertEqual([], self.check([("wiki/concepts/Existing.md", new, old)]))

    def test_new_dash_is_reported_but_protected_text_is_not(self):
        body = "\n".join([
            "`code \u2014 here`",
            "[[Page \u2014 Name]] and [x](https://e.com/a\u2014b)",
            "\"a quoted \u2014 line\"",
            "```\nfenced \u2014 code\n```",
            "Plain \u2014 prose.",
            "> Plain quote \u2014 is the model's own text.",
        ]) + "\n"
        found = self.check([("wiki/concepts/A.md", page(body=body), None)])
        lines = sorted(item.line for item in found if item.rule == "forbidden-character")
        self.assertEqual(2, len(lines), found)

    def test_lines_moved_between_files_are_history(self):
        hot_old = f"# Hot\n\n- Entry {DASH} archived now\n- Keep\n"
        hot_new = "# Hot\n\n- Keep\n"
        archive_old = "# Archive\n"
        archive_new = f"# Archive\n\n- Entry {DASH} archived now\n"
        self.assertEqual([], self.check([("wiki/hot.md", hot_new, hot_old),
                                         ("wiki/meta/Archive.md", archive_new, archive_old)]))

    def test_history_is_judged_per_file_whatever_the_bundle_order(self):
        line = f"Note {DASH} x\n"
        kept = page(body=line)
        for order in (0, 1):
            writes = [("wiki/concepts/B.md", page(body=line), None),
                      ("wiki/concepts/C.md", kept + "More.\n", kept)]
            found = self.check(writes if order == 0 else writes[::-1])
            self.assertEqual(["wiki/concepts/B.md"], [item.path for item in found], order)

    def test_links_need_the_whole_path_tail(self):
        self.assertFalse(cv.links_to("[[x/page]]", "wiki/a/xx/page.md"))
        self.assertTrue(cv.links_to("[[xx/page]]", "wiki/a/xx/page.md"))
        self.assertTrue(cv.links_to("[[wiki/a/xx/page|Page]]", "wiki/a/xx/page.md"))

    def test_exempt_paths_keep_their_dashes(self):
        self.assertEqual([], self.check([("wiki/folds/f.md", page(body=f"x {DASH} y\n"), None)]))

    def test_new_page_needs_its_folder_catalog_in_the_same_operation(self):
        new = page()
        catalog = "# Concepts\n\n- [[Existing]]\n"
        found = cv.check_writes([("wiki/concepts/New.md", new.encode(), None)], self.conventions,
                                lambda relative: catalog.encode() if relative == "wiki/concepts/_index.md" else None)
        self.assertEqual(["catalog"], [item.rule for item in found])
        listed = catalog + "- [[New|new page]]\n"
        self.assertEqual([], cv.check_writes(
            [("wiki/concepts/New.md", new.encode(), None),
             ("wiki/concepts/_index.md", listed.encode(), catalog.encode())],
            self.conventions, lambda relative: None))
        self.assertEqual([], cv.check_writes([("wiki/other/New.md", new.encode(), None)],
                                             self.conventions, lambda relative: None))

    def test_log_lines_are_judged_only_in_the_log(self):
        bad = LOG.replace("## 2026-10-22\n", "## 2026-10-23\n\n- **query→web** | X\n\n## 2026-10-22\n")
        self.assertEqual(["log-entry"], [item.rule for item in self.check([("wiki/log.md", bad, LOG)])])

    def test_replacement_keeps_headings_and_ranges_tight(self):
        text = page(body=f"## Head {DASH} anchor\n\nA {DASH} b, c{DASH}d, 2014{RANGE}2021, x {RANGE} y.\n")
        new, count = cv.replace_forbidden(text, self.conventions)
        self.assertEqual(4, count)
        self.assertIn(f"## Head {DASH} anchor", new)
        self.assertIn("A - b, c - d, 2014-2021, x - y.", new)
        self.assertEqual([], cv.forbidden_lines(new, self.conventions, headings=False))
        self.assertEqual(1, len(cv.forbidden_lines(new, self.conventions)))


class TransactionTests(Fixture):
    def test_without_a_conventions_file_nothing_changes(self):
        bundle = self.bundle([{"path": "wiki/concepts/New.md", "content": page(status="anything", body=f"a {DASH} b\n")}])
        self.assertTrue(inspect_bundle(self.vault, bundle)["valid"])

    def test_violations_refuse_the_operation(self):
        self.declare()
        self.assert_refused(self.bundle([
            {"path": "wiki/concepts/New.md", "content": page(status="stable")},
            {"path": "wiki/concepts/_index.md", "content": "# Concepts\n\n- [[Existing]]\n- [[New]]\n"},
        ]), "use 'evergreen'")
        self.assert_refused(self.bundle([{"path": "wiki/concepts/New.md", "content": page()}]), "_index.md")

    def test_a_clean_operation_applies(self):
        self.declare()
        bundle = self.bundle([
            {"path": "wiki/concepts/New.md", "content": page(body="A - b.\n")},
            {"path": "wiki/concepts/_index.md", "content": "# Concepts\n\n- [[Existing]]\n- [[New]]\n"},
            {"path": "wiki/log.md", "content": LOG.replace(
                "## 2026-10-22\n", "## 2026-10-23\n\n- **save** | New page\n\n## 2026-10-22\n")},
        ])
        self.assertEqual(3, len(apply_bundle(self.vault, bundle)["changed_paths"]))

    def test_a_broken_conventions_file_fails_closed(self):
        self.write(cv.CONVENTIONS_PATH, "{not json")
        with self.assertRaises(TransactionValidationError) as caught:
            inspect_bundle(self.vault, self.bundle([{"path": "wiki/a.md", "content": "# A\n"}]))
        self.assertEqual("INVALID_CONVENTIONS", caught.exception.code)


class LintTests(Fixture):
    def test_lint_reports_the_whole_vault_only_when_declared(self):
        report = lint_vault(self.vault)
        self.assertEqual([], report["convention_violations"])
        self.declare()
        self.write("wiki/concepts/Orphan.md", page())
        self.write("wiki/log.md", LOG.replace("- **save** | Older", f"- **save** | Older {DASH} history"))
        report = lint_vault(self.vault)
        self.assertNotIn(("wiki/log.md", "forbidden-character"),
                         {(item["path"], item["rule"]) for item in report["convention_violations"]})
        rules = sorted({(item["path"], item["rule"]) for item in report["convention_violations"]})
        self.assertIn(("wiki/concepts/Existing.md", "frontmatter"), rules)
        self.assertIn(("wiki/concepts/Existing.md", "forbidden-character"), rules)
        self.assertIn(("wiki/concepts/Orphan.md", "catalog"), rules)
        self.write(cv.CONVENTIONS_PATH, "[]")
        self.assertTrue(any(item["path"] == cv.CONVENTIONS_PATH
                            for item in lint_vault(self.vault)["configuration_errors"]))


class CommandTests(Fixture):
    def run_command(self, *args):
        return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                              encoding="utf-8", cwd=self.temporary.name)

    def test_log_entry_drafts_the_log_and_reports_hashes(self):
        out = Path(self.temporary.name) / "log.md"
        result = self.run_command(CORE, "log-entry", "--vault", self.vault, "--op", "save", "--title",
                                  "New page", "--detail", "[[New]]: created.", "--date", "2026-10-23",
                                  "--output", out)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(sha(LOG), report["expected_sha256"])
        drafted = out.read_bytes().decode("utf-8")
        self.assertEqual(sha(drafted), report["sha256"])
        self.assertIn("## 2026-10-23\n\n- **save** | New page\n  - [[New]]: created.\n\n## 2026-10-22", drafted)
        refused = self.run_command(CORE, "log-entry", "--vault", self.vault, "--op", "query→web",
                                   "--title", "x", "--output", out)
        self.assertEqual(2, refused.returncode)
        self.assertEqual(drafted, out.read_bytes().decode("utf-8"))

    def test_repairs_draft_bundles_that_apply_and_leave_no_findings(self):
        self.declare()
        self.write("wiki/concepts/Inline.md",
                   "---\nstatus: stable\nconfidence: medium-high\nretrieved: 2026-10-01\ntags: [a, \"b c\"]\n---\n\n"
                   f"# Inline {DASH} heading\n\nProse {DASH} here and \"quoted {DASH} text\".\n")
        self.write("wiki/concepts/Crlf.md", "---\r\nstatus: stable\r\ntags: [z]\r\n---\r\n\r\nA \u2014 b\r\n")
        self.write("wiki/concepts/Renamed.md", "---\nstate: stable\ntags: [ai, \"web: scraping\"]\n---\n\n# R\n")
        self.declare({**CONVENTIONS, "frontmatter": {
            **CONVENTIONS["frontmatter"],
            "property_aliases": {"retrieved": "date_fetched", "state": "status"}}})
        for kind in ("frontmatter", "text", "catalogs"):
            out = Path(self.temporary.name) / f"repair-{kind}"
            result = self.run_command(REPAIR, "--vault", self.vault, "--fix", kind, "--out", out)
            self.assertEqual(0, result.returncode, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["writes"], report)
            apply_bundle(self.vault, json.loads((out / "bundle.json").read_text(encoding="utf-8")))
        inline = (self.vault / "wiki/concepts/Inline.md").read_text(encoding="utf-8")
        self.assertIn("status: evergreen\nconfidence: medium\ndate_fetched: 2026-10-01\ntags:\n  - a\n  - b c\n",
                      inline)
        self.assertIn(f"# Inline {DASH} heading", inline)
        self.assertIn(f"Prose - here and \"quoted {DASH} text\".", inline)
        renamed = (self.vault / "wiki/concepts/Renamed.md").read_text(encoding="utf-8")
        self.assertIn("status: evergreen\ntags:\n  - ai\n  - \"web: scraping\"\n", renamed)
        crlf = (self.vault / "wiki/concepts/Crlf.md").read_bytes().decode("utf-8")
        self.assertEqual("---\r\nstatus: evergreen\r\ntags:\r\n  - z\r\n---\r\n\r\nA - b\r\n", crlf)
        catalog = (self.vault / "wiki/concepts/_index.md").read_text(encoding="utf-8")
        self.assertIn("## Uncatalogued", catalog)
        self.assertIn("- [[Crlf]]\n- [[Inline]]\n- [[Renamed]]\n", catalog)
        self.assertEqual([], lint_vault(self.vault)["convention_violations"])
        again = self.run_command(REPAIR, "--vault", self.vault, "--fix", "catalogs",
                                 "--out", Path(self.temporary.name) / "again")
        self.assertEqual(0, json.loads(again.stdout)["writes"])


class WorkflowTests(Fixture):
    def test_dated_bullet_log_satisfies_the_save_workflow(self):
        self.write("wiki/index.md", "# Index\n")
        self.write("wiki/hot.md", "# Hot\n")
        new_log = LOG.replace("## 2026-10-22\n", "## 2026-10-23\n\n- **save** | New\n\n## 2026-10-22\n")
        writes = [
            {"path": "wiki/concepts/New.md", "content": page()},
            {"path": "wiki/index.md", "content": "# Index\n\n[[wiki/concepts/New]]\n"},
            {"path": "wiki/log.md", "content": new_log},
            {"path": "wiki/hot.md", "content": "# Hot\n\n[[wiki/concepts/New]]\n"},
        ]
        bundle = self.bundle(writes, operation_type="save")
        bundle["workflow"] = {"catalogs": ["wiki/index.md"]}
        report = inspect_workflow(self.vault, bundle)
        self.assertNotIn("INVALID_BATCH_LOG", {item["code"] for item in report["workflow_findings"]}, report)
        self.assertNotIn("LOG_HISTORY_NOT_PRESERVED", {item["code"] for item in report["workflow_findings"]})
        bundle["writes"][2]["content"] = new_log.replace("- **save** | Older", "- **save** | Rewritten")
        codes = {item["code"] for item in inspect_workflow(self.vault, bundle)["workflow_findings"]}
        self.assertIn("LOG_HISTORY_NOT_PRESERVED", codes)


if __name__ == "__main__":
    unittest.main()
