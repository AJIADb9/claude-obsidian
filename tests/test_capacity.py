"""Capacity boundaries and reviewed legacy formatting recovery."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_obsidian.capacity import compaction_bundle, inspect_capacity
from claude_obsidian.ledgers import SOURCE_SCHEMA, CLAIM_SCHEMA
from claude_obsidian.transaction import apply_bundle, inspect_bundle, TransactionError


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.vault = Path(self.temporary.name) / 'vault'
        (self.vault / 'wiki/meta/ledgers').mkdir(parents=True)
        (self.vault / '.obsidian').mkdir()

    def test_absent_ledger_is_not_a_zero_byte_measurement(self):
        report = inspect_capacity(self.vault)
        self.assertTrue(report['ok'])
        self.assertTrue(all(x['status'] == 'absent' and x['size_bytes'] is None for x in report['ledgers']))

    def test_lossless_compaction_is_reviewed_and_recoverable(self):
        source = self.vault / 'wiki/meta/ledgers/source-ledger.json'
        claim = self.vault / 'wiki/meta/ledgers/claim-ledger.json'
        source.write_text(json.dumps({'schema': SOURCE_SCHEMA, 'generated_at': '2026-10-17T12:00:00Z', 'sources': {}}) + ' ' * (16 * 1024 * 1024))
        claim.write_text(json.dumps({'schema': CLAIM_SCHEMA, 'generated_at': '2026-10-17T12:00:00Z', 'claims': {}}))
        before = source.read_bytes()
        self.assertFalse(inspect_capacity(self.vault)['ok'])
        bundle = compaction_bundle(self.vault, operation_id='capacity-recovery')
        self.assertEqual(before, source.read_bytes())
        plan = inspect_bundle(self.vault, bundle)
        with self.assertRaises(TransactionError):
            apply_bundle(self.vault, bundle, approved_plan_sha256='0' * 64)
        self.assertEqual(before, source.read_bytes())
        apply_bundle(self.vault, bundle, approved_plan_sha256=plan['approval_sha256'])
        self.assertEqual(json.loads(before), json.loads(source.read_bytes()))
        backup = self.vault / '.vault-meta/transactions/capacity-recovery/backups/0000.original'
        # Backups are bound by the journal, regardless of sorted path position.
        journal = json.loads((backup.parent.parent / 'journal.json').read_text())
        entry = next(x for x in journal['writes'] if x['path'] == 'wiki/meta/ledgers/source-ledger.json')
        self.assertEqual(hashlib.sha256(before).hexdigest(), entry['original_sha256'])
        self.assertEqual(before, (backup.parent / entry['backup']).read_bytes())
        self.assertTrue(inspect_capacity(self.vault)['ok'])

    def test_compaction_never_prunes_records_to_fit(self):
        source = self.vault / 'wiki/meta/ledgers/source-ledger.json'
        original = json.dumps({'schema': SOURCE_SCHEMA, 'sources': {}, 'data': 'x' * 200})
        source.write_text(original)
        with mock.patch('claude_obsidian.capacity.MAX_CANONICAL_LEDGER_BYTES', 100):
            with self.assertRaises(TransactionError) as caught:
                compaction_bundle(self.vault, operation_id='too-large')
        self.assertEqual('LEDGER_MIGRATION_REQUIRED', caught.exception.code)
        self.assertEqual(original, source.read_text())
        self.assertFalse((self.vault / '.vault-meta').exists())

    def test_duplicate_json_keys_and_symlink_are_refused(self):
        source = self.vault / 'wiki/meta/ledgers/source-ledger.json'
        source.write_text('{"sources":{},"sources":{}}')
        with self.assertRaises(TransactionError):
            compaction_bundle(self.vault, operation_id='duplicate')
        source.unlink()
        outside = Path(self.temporary.name) / 'private.json'
        outside.write_text('{}')
        source.symlink_to(outside)
        with self.assertRaises(TransactionError):
            compaction_bundle(self.vault, operation_id='symlink')

    def test_numeric_tokens_and_escaped_string_values_are_preserved_exactly(self):
        source = self.vault / 'wiki/meta/ledgers/source-ledger.json'
        original = '{ "schema": "' + SOURCE_SCHEMA + '", "sources": {}, "custom_measurement": 1.0000000000000001, "custom_string": "a \\"quote\\" \\t text" }'
        source.write_text(original)
        plan = compaction_bundle(self.vault, operation_id='precise')
        text = plan['writes'][0]['content']
        self.assertIn('1.0000000000000001', text)
        self.assertIn('"a \\"quote\\" \\t text"', text)
        self.assertNotIn(':1.0,', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
