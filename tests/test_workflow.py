"""Observe workflow coupling independently of generic transaction safety."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_obsidian.transaction import BUNDLE_SCHEMA, RESULT_SCHEMA, TransactionError, inspect_bundle
from claude_obsidian.workflow import apply_workflow, inspect_workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.vault = Path(self.temporary.name) / 'vault'
        (self.vault / 'wiki').mkdir(parents=True)
        (self.vault / '.obsidian').mkdir()

    def bundle(self, *, catalog='wiki/index.md'):
        writes = [
            {'path': 'wiki/concepts/Example.md', 'mode': 'create', 'content': '# Example\n\nSelected content.\n'},
            {'path': catalog, 'mode': 'create', 'content': '# Catalog\n\n[[wiki/concepts/Example]]\n'},
            {'path': 'wiki/log.md', 'mode': 'create', 'content': '## [2026-10-17] save | Example\n\nSaved selected content.\n'},
            {'path': 'wiki/hot.md', 'mode': 'create', 'content': '# Recent\n\n[[wiki/concepts/Example]]\n'},
        ]
        return {'schema': BUNDLE_SCHEMA, 'operation_id': 'workflow-example', 'operation_type': 'save',
                'workflow': {'catalogs': [catalog]}, 'expected_hashes': {w['path']: None for w in writes}, 'writes': writes}

    def test_storage_valid_is_distinct_from_workflow_complete(self):
        bundle = self.bundle()
        bundle['writes'] = bundle['writes'][:1]
        bundle['expected_hashes'] = {'wiki/concepts/Example.md': None}
        self.assertTrue(inspect_bundle(self.vault, bundle)['valid'])
        result = inspect_workflow(self.vault, bundle)
        self.assertTrue(result['storage_valid'])
        self.assertFalse(result['workflow_complete'])
        with self.assertRaises(TransactionError):
            apply_workflow(self.vault, bundle, approved_plan_sha256=result['approval_sha256'])
        self.assertFalse((self.vault / '.vault-meta').exists())

    def test_complete_alternative_moc_applies_with_reviewed_hash(self):
        bundle = self.bundle(catalog='wiki/mocs/Projects.md')
        result = inspect_workflow(self.vault, bundle)
        self.assertTrue(result['workflow_complete'], result)
        applied = apply_workflow(self.vault, bundle, approved_plan_sha256=result['approval_sha256'])
        self.assertEqual(applied, apply_workflow(self.vault, bundle, approved_plan_sha256=result['approval_sha256']))
        self.assertIn('wiki/mocs/Projects.md', applied['changed_paths'])
        self.assertEqual('# Example\n\nSelected content.\n', (self.vault / 'wiki/concepts/Example.md').read_text())

    def test_unrelated_or_example_only_catalog_link_is_incomplete(self):
        for text in ['[[Other]]', '```markdown\n[[wiki/concepts/Example]]\n```', '<!-- [[wiki/concepts/Example]] -->']:
            bundle = self.bundle()
            bundle['writes'][1]['content'] = '# Catalog\n\n' + text + '\n'
            self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_protected_catalog_links_do_not_satisfy_navigation(self):
        for text in ('%% [[wiki/concepts/Example]] %%', '%%\n[[wiki/concepts/Example]]\n%%',
                     '    [[wiki/concepts/Example]]', r'\[[wiki/concepts/Example]]',
                     '$[[wiki/concepts/Example]]$', '````\n```\n[[wiki/concepts/Example]]\n```\n````'):
            with self.subTest(text=text):
                bundle = self.bundle()
                bundle['writes'][1]['content'] = '# Catalog\n\n' + text + '\n'
                self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_yaml_comment_catalog_links_do_not_satisfy_navigation_or_apply(self):
        for newline in ('\n', '\r\n'):
            for property_line in (
                '# related: [[wiki/concepts/Example]]',
                'summary: ordinary text # [[wiki/concepts/Example]]',
                'summary: "ordinary text" # [[wiki/concepts/Example]]',
            ):
                with self.subTest(newline=repr(newline), property_line=property_line):
                    bundle = self.bundle()
                    bundle['writes'][1]['content'] = newline.join((
                        '---',
                        'title: "Catalog"',
                        property_line,
                        '---',
                        '# Catalog',
                        '',
                        'No active navigation reference.',
                        '',
                    ))
                    report = inspect_workflow(self.vault, bundle)
                    self.assertFalse(report['workflow_complete'])
                    self.assertIn(
                        'MISSING_CATALOG_REFERENCE',
                        {item['code'] for item in report['workflow_findings']},
                    )
                    with self.assertRaises(TransactionError):
                        apply_workflow(
                            self.vault,
                            bundle,
                            approved_plan_sha256=report['approval_sha256'],
                        )
                    self.assertFalse((self.vault / '.vault-meta').exists())

    def test_active_quoted_property_wikilink_satisfies_navigation_and_applies(self):
        for label, newline in (('lf', '\n'), ('crlf', '\r\n')):
            with self.subTest(label=label):
                vault = Path(self.temporary.name) / f'active-property-{label}'
                (vault / 'wiki').mkdir(parents=True)
                (vault / '.obsidian').mkdir()
                bundle = self.bundle()
                bundle['operation_id'] = f'active-property-{label}'
                bundle['writes'][1]['content'] = newline.join((
                    '---',
                    'title: "Catalog"',
                    'related: "[[wiki/concepts/Example]]" # active reference',
                    '---',
                    '# Catalog',
                    '',
                    'Property navigation only.',
                    '',
                ))
                report = inspect_workflow(vault, bundle)
                self.assertTrue(report['workflow_complete'], report)
                applied = apply_workflow(
                    vault,
                    bundle,
                    approved_plan_sha256=report['approval_sha256'],
                )
                self.assertIn('wiki/concepts/Example.md', applied['changed_paths'])
                self.assertTrue((vault / 'wiki/concepts/Example.md').is_file())

    def test_valid_titled_and_angled_catalog_links(self):
        for link in ('[Example](<concepts/Example.md>)', '[Example](concepts/Example.md "A title")',
                     "[Example](concepts/Example.md 'A title')", '[Example](concepts/Example.md (A title))',
                     '`<!--` followed by [Example](concepts/Example.md)',
                     '- Parent\n    [Example](concepts/Example.md)'):
            with self.subTest(link=link):
                bundle = self.bundle()
                bundle['writes'][1]['content'] = '# Catalog\n\n' + link + '\n'
                self.assertTrue(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_list_fence_navigation_resumes_after_container_boundary(self):
        for suffix in ('\n[Example](concepts/Example.md)\n', '- [Example](concepts/Example.md)\n'):
            with self.subTest(suffix=suffix):
                bundle = self.bundle()
                bundle['writes'][1]['content'] = '- ```\n  sample\n' + suffix
                self.assertTrue(inspect_workflow(self.vault, bundle)['workflow_complete'])
        for content in ('- ```\n  [Example](concepts/Example.md)\n',
                        '- ```\n  sample\n  - [Example](concepts/Example.md)\n',
                        '```\n  sample\n\n[Example](concepts/Example.md)\n'):
            with self.subTest(content=content):
                bundle = self.bundle()
                bundle['writes'][1]['content'] = content
                self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_catalog_destinations_preserve_literal_math_punctuation(self):
        bundle = self.bundle()
        bundle['writes'][0]['path'] = 'wiki/concepts/Price$10$.md'
        bundle['expected_hashes'].pop('wiki/concepts/Example.md')
        bundle['expected_hashes'][bundle['writes'][0]['path']] = None
        for link in ('[Price](concepts/Price$10$.md)', '[Price](<concepts/Price$10$.md>)',
                     '[[wiki/concepts/Price$10$]]'):
            with self.subTest(link=link):
                bundle['writes'][1]['content'] = link + '\n'
                self.assertTrue(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_existing_orphan_cannot_use_unchanged_unrelated_catalog(self):
        for operation in ('save', 'ingest'):
            with self.subTest(operation=operation):
                bundle = self.bundle()
                bundle['operation_type'] = operation
                bundle['writes'][2]['content'] = bundle['writes'][2]['content'].replace('save', operation)
                for index, content in ((0, '# Previous\n'), (1, '# Empty catalog\n')):
                    write = bundle['writes'][index]
                    path = self.vault / write['path']
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                    write['mode'] = 'replace'
                    bundle['expected_hashes'][write['path']] = hashlib.sha256(path.read_bytes()).hexdigest()
                bundle['writes'][1]['content'] = '# Empty catalog\n'
                report = inspect_workflow(self.vault, bundle)
                self.assertIn('MISSING_CATALOG_REFERENCE', {x['code'] for x in report['workflow_findings']})

    def test_existing_indexed_note_accepts_hash_bound_unchanged_catalog_and_replay(self):
        bundle = self.bundle()
        for index, content in ((0, '# Previous\n'), (1, bundle['writes'][1]['content'])):
            write = bundle['writes'][index]
            path = self.vault / write['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            write['mode'] = 'replace'
            bundle['expected_hashes'][write['path']] = hashlib.sha256(path.read_bytes()).hexdigest()
        old_catalog = (self.vault / 'wiki/index.md').read_bytes()
        report = inspect_workflow(self.vault, bundle)
        self.assertTrue(report['workflow_complete'], report)
        applied = apply_workflow(self.vault, bundle, approved_plan_sha256=report['approval_sha256'])
        self.assertEqual(old_catalog, (self.vault / 'wiki/index.md').read_bytes())
        self.assertEqual(applied, apply_workflow(self.vault, bundle, approved_plan_sha256=report['approval_sha256']))

    def test_changed_bundle_and_wrong_approval_do_not_mutate(self):
        bundle = self.bundle()
        approval = inspect_workflow(self.vault, bundle)['approval_sha256']
        bundle['writes'][0]['content'] += 'Expanded scope.\n'
        with self.assertRaises(TransactionError):
            apply_workflow(self.vault, bundle, approved_plan_sha256=approval)
        self.assertFalse((self.vault / '.vault-meta').exists())

    def test_markdown_catalog_navigation_cannot_escape_the_vault(self):
        for destination in ('../../wiki/concepts/Example.md', '%2e%2e/%2e%2e/wiki/concepts/Example.md',
                            '/concepts/Example.md', 'https://example.com/concepts/Example.md',
                            '../..//wiki/concepts/Example.md', '%5cconcepts/Example.md'):
            bundle = self.bundle()
            bundle['writes'][1]['content'] = f'# Catalog\n\n[Example]({destination})\n'
            self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'], destination)
        for destination in ('concepts/Example.md', '../wiki/concepts/Example.md', 'concepts/Example.md#Heading'):
            bundle = self.bundle()
            bundle['writes'][1]['content'] = f'# Catalog\n\n[Example]({destination})\n'
            self.assertTrue(inspect_workflow(self.vault, bundle)['workflow_complete'], destination)

    def test_each_selected_note_needs_prospective_navigation(self):
        bundle = self.bundle()
        second = {'path': 'wiki/concepts/Second.md', 'mode': 'create', 'content': '# Second\n'}
        bundle['writes'].append(second)
        bundle['expected_hashes'][second['path']] = None
        report = inspect_workflow(self.vault, bundle)
        self.assertEqual(['wiki/concepts/Second.md'], [finding['path'] for finding in report['workflow_findings']
                                                    if finding['code'] == 'MISSING_CATALOG_REFERENCE'])
        bundle['writes'][1]['content'] += '\n[[wiki/concepts/Second]]\n'
        self.assertTrue(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_existing_catalog_drift_rejects_before_mutation(self):
        bundle = self.bundle()
        catalog = self.vault / 'wiki/index.md'
        catalog.write_text(bundle['writes'][1]['content'])
        bundle['writes'][1]['mode'] = 'replace'
        bundle['expected_hashes']['wiki/index.md'] = hashlib.sha256(catalog.read_bytes()).hexdigest()
        approval = inspect_workflow(self.vault, bundle)['approval_sha256']
        catalog.write_text('# Catalog changed after review\n')
        with self.assertRaises(TransactionError):
            apply_workflow(self.vault, bundle, approved_plan_sha256=approval)
        self.assertFalse((self.vault / 'wiki/concepts/Example.md').exists())
        self.assertFalse((self.vault / '.vault-meta').exists())

    def test_angled_and_titled_links_retain_decoding_and_confinement(self):
        bundle = self.bundle()
        bundle['writes'][0]['path'] = 'wiki/concepts/Example (one) #1.md'
        bundle['expected_hashes'].pop('wiki/concepts/Example.md')
        bundle['expected_hashes'][bundle['writes'][0]['path']] = None
        for link in ('[Example](<concepts/Example (one) %231.md> "A title")',
                     '[Example](concepts/Example%20(one)%20%231.md "A title")'):
            bundle['writes'][1]['content'] = '# Catalog\n\n' + link + '\n'
            self.assertTrue(inspect_workflow(self.vault, bundle)['workflow_complete'], link)
        for destination in ('<../../wiki/concepts/Example (one) %231.md>',
                            '<%2e%2e/%2e%2e/wiki/concepts/Example (one) %231.md>',
                            '<%5cconcepts/Example (one) %231.md>',
                            '<concepts/%0aExample (one) %231.md>',
                            '<&#10;concepts/Example (one) %231.md>'):
            bundle['writes'][1]['content'] = f'# Catalog\n\n[Example]({destination} "A title")\n'
            self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'], destination)

    def test_content_file_is_frozen_and_drift_rejects_approval(self):
        draft = Path(self.temporary.name) / 'note.md'
        draft.write_text('# Example\n\nSelected content.\n')
        bundle = self.bundle()
        bundle['writes'][0].pop('content')
        bundle['writes'][0]['content_file'] = 'note.md'
        bundle['writes'][0]['sha256'] = hashlib.sha256(draft.read_bytes()).hexdigest()
        source = Path(self.temporary.name) / 'bundle.json'
        source.write_text(json.dumps(bundle))
        result = inspect_workflow(self.vault, source)
        draft.write_text('# Different\n')
        with self.assertRaises(TransactionError):
            apply_workflow(self.vault, source, approved_plan_sha256=result['approval_sha256'])
        self.assertFalse((self.vault / '.vault-meta').exists())

    def test_hot_limit_and_preserved_log_history(self):
        old = '## [2026-10-16] save | Earlier\n\nEarlier content.\n'
        (self.vault / 'wiki/log.md').write_text(old)
        bundle = self.bundle()
        bundle['expected_hashes']['wiki/log.md'] = hashlib.sha256(old.encode()).hexdigest()
        bundle['writes'][2]['mode'] = 'replace'
        self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'])
        bundle['writes'][2]['content'] += '\n' + old
        self.assertTrue(inspect_workflow(self.vault, bundle)['workflow_complete'])
        bundle['writes'][3]['content'] = 'word ' * 501
        self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_catalog_policy_is_confined_and_hash_bound(self):
        for path in ['../outside.md', 'wiki/../outside.md', 'wiki/hot.md', 'wiki/log.md', '/wiki/index.md',
                     'wiki/.hidden/Index.md', 'wiki/mocs/.Index.md', 'wiki/mocs/.archive/Index.md']:
            bundle = self.bundle()
            bundle['workflow']['catalogs'] = [path]
            self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_hidden_selected_notes_cannot_satisfy_save_or_navigation(self):
        for path in ('wiki/.hidden/Example.md', 'wiki/concepts/.Example.md'):
            with self.subTest(path=path):
                bundle = self.bundle()
                bundle['expected_hashes'].pop(bundle['writes'][0]['path'])
                bundle['writes'][0]['path'] = path
                bundle['expected_hashes'][path] = None
                bundle['writes'][1]['content'] = f'[[{path}]]\n'
                report = inspect_workflow(self.vault, bundle)
                self.assertTrue(report['storage_valid'])
                codes = {item['code'] for item in report['workflow_findings']}
                self.assertIn('INVALID_SELECTED_NOTE', codes)
                self.assertIn('MISSING_SELECTED_NOTE', codes)
                with self.assertRaises(TransactionError):
                    apply_workflow(self.vault, bundle, approved_plan_sha256=report['approval_sha256'])
                self.assertFalse((self.vault / '.vault-meta').exists())

    def test_hidden_catalog_write_is_incomplete_and_cannot_apply(self):
        for path in ('wiki/.hidden/Index.md', 'wiki/mocs/.Index.md'):
            with self.subTest(path=path):
                bundle = self.bundle(catalog=path)
                report = inspect_workflow(self.vault, bundle)
                self.assertIn('INVALID_CATALOGS', {item['code'] for item in report['workflow_findings']})
                with self.assertRaises(TransactionError):
                    apply_workflow(self.vault, bundle, approved_plan_sha256=report['approval_sha256'])
                self.assertFalse((self.vault / '.vault-meta').exists())

    def test_visible_selected_note_does_not_hide_invalid_extra_note(self):
        bundle = self.bundle()
        bundle['writes'].append({'path': 'wiki/concepts/.Secret.md', 'mode': 'create', 'content': '# Secret\n'})
        bundle['expected_hashes']['wiki/concepts/.Secret.md'] = None
        self.assertIn('INVALID_SELECTED_NOTE', {
            item['code'] for item in inspect_workflow(self.vault, bundle)['workflow_findings']
        })

    def test_metadata_and_raw_writes_are_not_selected_notes(self):
        bundle = self.bundle()
        bundle['operation_type'] = 'ingest'
        bundle['writes'][2]['content'] = bundle['writes'][2]['content'].replace('save', 'ingest')
        for path, content in (('wiki/meta/Review.md', '# Review\n'),
                              ('.raw/.capture/source.md', '# Immutable source\n')):
            bundle['writes'].append({'path': path, 'mode': 'create', 'content': content})
            bundle['expected_hashes'][path] = None
        codes = {item['code'] for item in inspect_workflow(self.vault, bundle)['workflow_findings']}
        self.assertNotIn('INVALID_SELECTED_NOTE', codes)
        self.assertNotIn('MISSING_CATALOG_REFERENCE', codes)
        self.assertEqual({'MISSING_PROVENANCE_WRITE'}, codes)

    def test_batch_log_requires_a_real_iso_calendar_date(self):
        for value, valid in (('2026-99-99', False), ('2026-02-29', False), ('0000-01-01', False),
                             ('2026-04-31', False), ('2026-2-01', False), ('2024-02-29', True),
                             ('2026-10-17', True)):
            with self.subTest(value=value):
                bundle = self.bundle()
                bundle['writes'][2]['content'] = f'## [{value}] save | Example\n'
                self.assertEqual(valid, inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_invalid_utf8_existing_log_is_a_bounded_api_and_cli_error(self):
        old_log = b'\xff legacy log\n'
        log_path = self.vault / 'wiki/log.md'
        log_path.write_bytes(old_log)
        bundle = self.bundle()
        bundle['operation_id'] = 'invalid-utf8-log'
        bundle['writes'][2]['mode'] = 'replace'
        bundle['expected_hashes']['wiki/log.md'] = hashlib.sha256(old_log).hexdigest()
        source = Path(self.temporary.name) / 'invalid-utf8-log.json'
        source.write_text(json.dumps(bundle), encoding='utf-8')
        before = {path.relative_to(self.vault): (path.read_bytes(), path.stat().st_mode)
                  for path in self.vault.rglob('*') if path.is_file()}

        with self.assertRaises(TransactionError) as caught:
            inspect_workflow(self.vault, source)
        self.assertEqual('WORKFLOW_LOG_NOT_TEXT', caught.exception.code)
        run = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/claude-obsidian.py'),
             'workflow', 'inspect', str(source), '--vault', str(self.vault)],
            text=True, capture_output=True, cwd=self.temporary.name, timeout=20,
        )
        self.assertEqual(2, run.returncode, run.stderr)
        self.assertIn('ERR WORKFLOW_LOG_NOT_TEXT:', run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertEqual(before, {
            path.relative_to(self.vault): (path.read_bytes(), path.stat().st_mode)
            for path in self.vault.rglob('*') if path.is_file()
        })
        self.assertFalse((self.vault / '.vault-meta').exists())

    def completed_content_file(self):
        draft = Path(self.temporary.name) / 'note.md'
        draft.write_text('# Example\n\nSelected content.\n')
        bundle = self.bundle()
        bundle['writes'][0].pop('content')
        bundle['writes'][0].update(content_file='note.md', sha256=hashlib.sha256(draft.read_bytes()).hexdigest())
        source = Path(self.temporary.name) / 'bundle.json'
        source.write_text(json.dumps(bundle))
        approval = inspect_workflow(self.vault, source)['approval_sha256']
        result = apply_workflow(self.vault, source, approved_plan_sha256=approval)
        return bundle, source, draft, approval, result

    def test_completed_content_file_replay_after_disposable_draft_removed(self):
        _, source, draft, approval, result = self.completed_content_file()
        draft.unlink()
        before = {path.relative_to(self.vault): path.read_bytes() for path in self.vault.rglob('*') if path.is_file()}
        self.assertEqual(result, apply_workflow(self.vault, source, approved_plan_sha256=approval))
        self.assertEqual(before, {path.relative_to(self.vault): path.read_bytes() for path in self.vault.rglob('*') if path.is_file()})

    def test_completed_replay_corrupt_json_and_utf8_are_bounded_cli_errors(self):
        bundle = self.bundle()
        source = Path(self.temporary.name) / 'bundle.json'
        source.write_text(json.dumps(bundle), encoding='utf-8')
        approval = inspect_workflow(self.vault, source)['approval_sha256']
        apply_workflow(self.vault, source, approved_plan_sha256=approval)
        result_path = self.vault / '.vault-meta/transactions/workflow-example/changed-paths.json'
        valid_result = json.loads(result_path.read_text(encoding='utf-8'))
        corruptions = [
            b'{"schema":"first","schema":"second"}\n',
            b'{"schema":"invalid-utf8","value":"\xff"}\n',
            b'[]\n',
            b'null\n',
            b'{}\n',
            b'{"schema":"claude-obsidian.transaction-result.v1","status":"complete"}\n',
        ]
        for field in ('bundle_sha256', 'expanded_bundle_sha256', 'approval_sha256'):
            for mutation in ('missing', 'nonstring', 'bad-hash'):
                changed = json.loads(json.dumps(valid_result))
                if mutation == 'missing':
                    changed.pop(field)
                elif mutation == 'nonstring':
                    changed[field] = []
                else:
                    changed[field] = 'g' * 64
                corruptions.append(json.dumps(changed).encode('utf-8'))
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                result_path.write_bytes(corruption)
                before = {path.relative_to(self.vault): (path.read_bytes(), path.stat().st_mode)
                          for path in self.vault.rglob('*') if path.is_file()}
                with self.assertRaises(TransactionError) as caught:
                    apply_workflow(self.vault, source, approved_plan_sha256=approval)
                self.assertEqual('CORRUPT_RESULT', caught.exception.code)
                run = subprocess.run(
                    [sys.executable, '-B', str(ROOT / 'scripts/claude-obsidian.py'),
                     'workflow', 'apply', str(source), '--vault', str(self.vault),
                     '--approved-plan-sha256', approval],
                    text=True, capture_output=True, cwd=self.temporary.name, timeout=20,
                )
                self.assertEqual(3, run.returncode, run.stderr)
                self.assertIn('ERR CORRUPT_RESULT:', run.stderr)
                self.assertNotIn('Traceback', run.stderr)
                self.assertEqual(before, {
                    path.relative_to(self.vault): (path.read_bytes(), path.stat().st_mode)
                    for path in self.vault.rglob('*') if path.is_file()
                })

    def test_completed_replay_malformed_writes_are_structurally_rejected(self):
        bundle = self.bundle()
        source = Path(self.temporary.name) / 'bundle.json'
        source.write_text(json.dumps(bundle), encoding='utf-8')
        approval = inspect_workflow(self.vault, source)['approval_sha256']
        apply_workflow(self.vault, source, approved_plan_sha256=approval)
        malformed = dict(bundle)
        malformed['writes'] = [None]
        source.write_text(json.dumps(malformed), encoding='utf-8')
        before = {path.relative_to(self.vault): (path.read_bytes(), path.stat().st_mode)
                  for path in self.vault.rglob('*') if path.is_file()}
        with self.assertRaises(TransactionError) as caught:
            apply_workflow(self.vault, source, approved_plan_sha256=approval)
        self.assertEqual('INVALID_WRITE', caught.exception.code)
        run = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/claude-obsidian.py'),
             'workflow', 'apply', str(source), '--vault', str(self.vault),
             '--approved-plan-sha256', approval],
            text=True, capture_output=True, cwd=self.temporary.name, timeout=20,
        )
        self.assertEqual(2, run.returncode, run.stderr)
        self.assertIn('ERR INVALID_WRITE:', run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertEqual(before, {
            path.relative_to(self.vault): (path.read_bytes(), path.stat().st_mode)
            for path in self.vault.rglob('*') if path.is_file()
        })

    def test_result_only_receipt_cannot_certify_missing_draft_and_absent_coupled_pages(self):
        draft = Path(self.temporary.name) / 'note.md'
        draft.write_text('# Example\n\nSelected content.\n')
        bundle = self.bundle()
        bundle['writes'][0].pop('content')
        bundle['writes'][0].update(content_file='note.md', sha256=hashlib.sha256(draft.read_bytes()).hexdigest())
        source = Path(self.temporary.name) / 'bundle.json'
        source.write_text(json.dumps(bundle))
        plan = inspect_workflow(self.vault, source)
        target = self.vault / 'wiki/concepts/Example.md'
        target.parent.mkdir(parents=True)
        target.write_bytes(draft.read_bytes())
        draft.unlink()
        operation = self.vault / '.vault-meta/transactions' / bundle['operation_id']
        operation.mkdir(parents=True)
        forged = {
            'schema': RESULT_SCHEMA, 'operation_id': bundle['operation_id'],
            'operation_type': 'save', 'status': 'complete',
            'bundle_sha256': plan['plan']['input_bundle_sha256'],
            'approval_sha256': plan['approval_sha256'],
            'changed_paths': [], 'hashes': {}, 'modes': {},
        }
        (operation / 'changed-paths.json').write_text(json.dumps(forged))
        before = {path.relative_to(self.vault): (path.read_bytes() if path.is_file() else None, path.stat().st_mode)
                  for path in self.vault.rglob('*')}
        with self.assertRaises(TransactionError) as caught:
            apply_workflow(self.vault, source, approved_plan_sha256=plan['approval_sha256'])
        self.assertEqual('CORRUPT_RESULT', caught.exception.code)
        self.assertEqual(before, {path.relative_to(self.vault): (path.read_bytes() if path.is_file() else None, path.stat().st_mode)
                                  for path in self.vault.rglob('*')})
        for relative in ('wiki/index.md', 'wiki/log.md', 'wiki/hot.md'):
            self.assertFalse((self.vault / relative).exists())
        self.assertEqual(bundle['writes'][0]['sha256'], hashlib.sha256(target.read_bytes()).hexdigest())

    def test_completed_content_file_replay_does_not_ignore_available_draft_drift(self):
        _, source, draft, approval, _ = self.completed_content_file()
        draft.write_text('# Changed draft\n')
        with self.assertRaises(TransactionError) as caught:
            apply_workflow(self.vault, source, approved_plan_sha256=approval)
        self.assertEqual('CONTENT_HASH_MISMATCH', caught.exception.code)

    def test_available_original_draft_cannot_mask_completed_target_drift(self):
        _, source, _, approval, _ = self.completed_content_file()
        target = self.vault / 'wiki/concepts/Example.md'
        target.write_text('# Changed target\n')
        with self.assertRaises(TransactionError) as caught:
            apply_workflow(self.vault, source, approved_plan_sha256=approval)
        self.assertEqual('OPERATION_RESULT_DRIFT', caught.exception.code)
        self.assertEqual('# Changed target\n', target.read_text())

    def test_missing_draft_replay_never_follows_a_target_symlink(self):
        _, source, draft, approval, _ = self.completed_content_file()
        original = draft.read_bytes()
        draft.unlink()
        outside = Path(self.temporary.name) / 'outside.md'
        outside.write_bytes(original)
        target = self.vault / 'wiki/concepts/Example.md'
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaises(TransactionError):
            apply_workflow(self.vault, source, approved_plan_sha256=approval)
        self.assertEqual(original, outside.read_bytes())

    def test_missing_draft_replay_rejects_changed_bundle_and_approval(self):
        bundle, source, draft, approval, _ = self.completed_content_file()
        draft.unlink()
        for mutation in ('declared_hash', 'expected_hash', 'target', 'mode', 'operation', 'inline_content'):
            with self.subTest(mutation=mutation):
                changed = json.loads(json.dumps(bundle))
                if mutation == 'declared_hash':
                    changed['writes'][0]['sha256'] = '0' * 64
                elif mutation == 'expected_hash':
                    changed['expected_hashes']['wiki/concepts/Example.md'] = '0' * 64
                elif mutation == 'target':
                    changed['writes'][0]['path'] = 'wiki/concepts/Other.md'
                elif mutation == 'mode':
                    changed['writes'][0]['mode'] = 'replace'
                elif mutation == 'operation':
                    changed['operation_type'] = 'ingest'
                else:
                    changed['writes'][1]['content'] += 'Changed catalog.\n'
                source.write_text(json.dumps(changed))
                with self.assertRaises(TransactionError):
                    apply_workflow(self.vault, source, approved_plan_sha256=approval)
        source.write_text(json.dumps(bundle))
        with self.assertRaises(TransactionError) as caught:
            apply_workflow(self.vault, source, approved_plan_sha256='0' * 64)
        self.assertEqual('PLAN_CHANGED', caught.exception.code)

    def test_missing_draft_replay_rejects_target_and_other_result_drift(self):
        _, source, draft, approval, _ = self.completed_content_file()
        draft.unlink()
        for relative in ('wiki/concepts/Example.md', 'wiki/index.md'):
            with self.subTest(relative=relative):
                target = self.vault / relative
                original = target.read_bytes()
                target.write_text('# Changed result\n')
                with self.assertRaises(TransactionError) as caught:
                    apply_workflow(self.vault, source, approved_plan_sha256=approval)
                self.assertEqual('OPERATION_RESULT_DRIFT', caught.exception.code)
                self.assertEqual('# Changed result\n', target.read_text())
                target.write_bytes(original)

    def test_missing_draft_replay_rejects_file_mode_drift(self):
        _, source, draft, approval, _ = self.completed_content_file()
        draft.unlink()
        target = self.vault / 'wiki/concepts/Example.md'
        target.chmod(0o600 if target.stat().st_mode & 0o777 != 0o600 else 0o644)
        with self.assertRaises(TransactionError) as caught:
            apply_workflow(self.vault, source, approved_plan_sha256=approval)
        self.assertEqual('OPERATION_RESULT_DRIFT', caught.exception.code)

    def completed_addressed_content_file(self, newline=b'\n', already_addressed=False, prefix='c'):
        ending = 'crlf' if newline == b'\r\n' else 'lf'
        case = Path(self.temporary.name) / f'addressed-{ending}-{already_addressed}-{prefix}'
        vault = case / 'vault'
        (vault / 'wiki').mkdir(parents=True)
        (vault / '.obsidian').mkdir()
        lines = [b'---', b'title: Example', b'---', b'# Example', b'', b'Selected content.', b'']
        if already_addressed:
            lines.insert(1, f'address: {prefix}-000001'.encode('ascii'))
        draft = case / 'note.md'
        draft.write_bytes(newline.join(lines))
        bundle = self.bundle()
        bundle['operation_type'] = 'ingest'
        bundle['writes'][2]['content'] = bundle['writes'][2]['content'].replace('save', 'ingest')
        bundle['writes'][0].pop('content')
        bundle['writes'][0].update(content_file='note.md', sha256=hashlib.sha256(draft.read_bytes()).hexdigest())
        bundle['address_requests'] = [{'path': 'wiki/concepts/Example.md', 'prefix': prefix}]
        for name, field in (('source', 'sources'), ('claim', 'claims')):
            path = f'wiki/meta/ledgers/{name}-ledger.json'
            content = json.dumps({'schema': f'claude-obsidian.{name}-ledger.v1',
                                  'generated_at': '2026-10-17T00:00:00Z', field: {}})
            bundle['writes'].append({'path': path, 'mode': 'create', 'content': content})
            bundle['expected_hashes'][path] = None
        source = case / 'bundle.json'
        source.write_text(json.dumps(bundle))
        approval = inspect_workflow(vault, source)['approval_sha256']
        result = apply_workflow(vault, source, approved_plan_sha256=approval)
        return vault, bundle, source, draft, approval, result

    def test_managed_address_missing_draft_replay_preserves_lf_crlf_and_existing_address(self):
        for newline in (b'\n', b'\r\n'):
            for already_addressed in (False, True):
                with self.subTest(newline=newline, already_addressed=already_addressed):
                    vault, _, source, draft, approval, result = self.completed_addressed_content_file(newline, already_addressed)
                    target = vault / 'wiki/concepts/Example.md'
                    committed = target.read_bytes()
                    self.assertEqual(1, committed.count(b'address: c-000001'))
                    self.assertIn(b'address: c-000001' + newline, committed)
                    draft.unlink()
                    self.assertEqual(result, apply_workflow(vault, source, approved_plan_sha256=approval))
                    self.assertEqual(committed, target.read_bytes())

    def test_managed_link_address_missing_draft_replays(self):
        vault, _, source, draft, approval, result = self.completed_addressed_content_file(prefix='l')
        self.assertIn(b'address: l-000001\n', (vault / 'wiki/concepts/Example.md').read_bytes())
        draft.unlink()
        self.assertEqual(result, apply_workflow(vault, source, approved_plan_sha256=approval))

    def test_managed_address_missing_draft_rejects_changed_request_and_input_hash(self):
        vault, bundle, source, draft, approval, _ = self.completed_addressed_content_file()
        draft.unlink()
        for mutation in ('prefix', 'path', 'input_hash', 'expanded_hash'):
            with self.subTest(mutation=mutation):
                changed = json.loads(json.dumps(bundle))
                if mutation == 'prefix':
                    changed['address_requests'][0]['prefix'] = 'l'
                elif mutation == 'path':
                    changed['address_requests'][0]['path'] = 'wiki/concepts/Other.md'
                elif mutation == 'input_hash':
                    changed['writes'][0]['sha256'] = '0' * 64
                else:
                    changed['writes'][0]['sha256'] = hashlib.sha256((vault / 'wiki/concepts/Example.md').read_bytes()).hexdigest()
                source.write_text(json.dumps(changed))
                with self.assertRaises(TransactionError):
                    apply_workflow(vault, source, approved_plan_sha256=approval)

    def test_managed_address_inverse_does_not_hide_target_or_metadata_drift(self):
        vault, _, source, draft, approval, _ = self.completed_addressed_content_file()
        draft.unlink()
        for path, old, new in (
            ('wiki/concepts/Example.md', b'c-000001', b'c-999999'),
            ('wiki/concepts/Example.md', b'Selected content.', b'Changed content.'),
            ('.raw/.manifest.json', b'c-000001', b'c-999999'),
        ):
            with self.subTest(path=path, old=old):
                target = vault / path
                original = target.read_bytes()
                target.write_bytes(original.replace(old, new))
                with self.assertRaises(TransactionError) as caught:
                    apply_workflow(vault, source, approved_plan_sha256=approval)
                self.assertEqual('OPERATION_RESULT_DRIFT', caught.exception.code)
                self.assertEqual(original.replace(old, new), target.read_bytes())
                target.write_bytes(original)

    def test_ingest_requires_both_provenance_ledgers(self):
        bundle = self.bundle()
        bundle['operation_type'] = 'ingest'
        bundle['writes'][2]['content'] = bundle['writes'][2]['content'].replace('save', 'ingest')
        findings = inspect_workflow(self.vault, bundle)['workflow_findings']
        self.assertEqual(2, sum(x['code'] == 'MISSING_PROVENANCE_WRITE' for x in findings))

    def test_generic_scope_is_not_reclassified_as_a_named_workflow(self):
        bundle = self.bundle()
        bundle['operation_type'] = 'generic'
        self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_existing_note_update_still_requires_catalog_write(self):
        bundle = self.bundle()
        path = self.vault / 'wiki/concepts/Example.md'
        path.parent.mkdir()
        path.write_text('# Previous\n')
        bundle['writes'][0]['mode'] = 'replace'
        bundle['expected_hashes']['wiki/concepts/Example.md'] = hashlib.sha256(path.read_bytes()).hexdigest()
        bundle['writes'] = [w for w in bundle['writes'] if w['path'] != 'wiki/index.md']
        bundle['expected_hashes'].pop('wiki/index.md')
        report = inspect_workflow(self.vault, bundle)
        self.assertFalse(report['workflow_complete'])
        self.assertIn('MISSING_CATALOG_WRITE', {x['code'] for x in report['workflow_findings']})

    def test_empty_log_or_hot_context_is_incomplete(self):
        for index in (2, 3):
            bundle = self.bundle()
            bundle['writes'][index]['content'] = ''
            self.assertFalse(inspect_workflow(self.vault, bundle)['workflow_complete'])

    def test_content_file_requires_its_declared_hash(self):
        draft = Path(self.temporary.name) / 'note.md'
        draft.write_text('# Example\n')
        bundle = self.bundle()
        bundle['writes'][0].pop('content')
        bundle['writes'][0]['content_file'] = 'note.md'
        path = Path(self.temporary.name) / 'bundle.json'
        path.write_text(json.dumps(bundle))
        with self.assertRaises(TransactionError) as caught:
            inspect_workflow(self.vault, path)
        self.assertEqual('CONTENT_HASH_REQUIRED', caught.exception.code)


if __name__ == '__main__':
    unittest.main(verbosity=2)
