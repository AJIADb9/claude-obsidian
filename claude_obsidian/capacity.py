"""Bounded local ledger capacity inspection and lossless compaction plans."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .json_utils import strict_json_loads
from .limits import CANONICAL_LEDGER_PATHS, MAX_CANONICAL_LEDGER_BYTES
from .transaction import BUNDLE_SCHEMA, TransactionValidationError, read_vault_regular

MAX_LEGACY_READ_BYTES = 64 * 1024 * 1024


def inspect_capacity(vault: Path | str) -> dict[str, Any]:
    ledgers = []
    for relative in CANONICAL_LEDGER_PATHS:
        try:
            raw = read_vault_regular(Path(vault), relative, limit=MAX_LEGACY_READ_BYTES)
        except TransactionValidationError as exc:
            ledgers.append({'path': relative, 'status': 'unreadable', 'code': exc.code,
                            'message': str(exc), 'read_limit_bytes': MAX_LEGACY_READ_BYTES})
            continue
        size = len(raw) if raw is not None else 0
        status = ('absent' if raw is None else 'over_capacity' if size > MAX_CANONICAL_LEDGER_BYTES
                  else 'near_capacity' if size >= MAX_CANONICAL_LEDGER_BYTES * 0.85 else 'within_capacity')
        ledgers.append({'path': relative, 'size_bytes': size if raw is not None else None,
                        'limit_bytes': MAX_CANONICAL_LEDGER_BYTES, 'status': status,
                        'sha256': hashlib.sha256(raw).hexdigest() if raw is not None else None})
    return {'schema': 'claude-obsidian.capacity.v1', 'ledgers': ledgers,
            'ok': all(x['status'] not in {'over_capacity', 'unreadable'} for x in ledgers),
            'recovery': 'Preview capacity compact; it only removes JSON formatting. If records still exceed the limit, preserve the source and request an explicitly designed migration. Never truncate ledger records or raw sources.'}


def compaction_bundle(vault: Path | str, *, operation_id: str) -> dict[str, Any]:
    writes = []
    hashes = {}
    for relative in CANONICAL_LEDGER_PATHS:
        raw = read_vault_regular(Path(vault), relative, limit=MAX_LEGACY_READ_BYTES)
        if raw is None:
            continue
        try:
            document = strict_json_loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise TransactionValidationError('INVALID_PROVENANCE_LEDGER', f'{relative}: cannot losslessly compact invalid JSON') from exc
        if not isinstance(document, dict):
            raise TransactionValidationError('INVALID_PROVENANCE_LEDGER', f'{relative}: root must be an object')
        # Remove only JSON whitespace outside strings. Re-serialization would
        # round high-precision numeric tokens through Python binary floats.
        # The strict parse above checks syntax and duplicate keys; original
        # number and string spellings are retained byte for byte.
        characters = []
        in_string = False
        escaped = False
        for character in raw.decode('utf-8'):
            if in_string:
                characters.append(character)
                if escaped:
                    escaped = False
                elif character == '\\':
                    escaped = True
                elif character == '"':
                    in_string = False
            elif character == '"':
                in_string = True
                characters.append(character)
            elif character not in ' \t\r\n':
                characters.append(character)
        compact = ''.join(characters) + '\n'
        if len(compact.encode('utf-8')) > MAX_CANONICAL_LEDGER_BYTES:
            raise TransactionValidationError('LEDGER_MIGRATION_REQUIRED', f'{relative}: lossless formatting compaction cannot fit all records within {MAX_CANONICAL_LEDGER_BYTES} bytes; no records were removed and no files changed')
        if compact.encode('utf-8') != raw:
            writes.append({'path': relative, 'mode': 'replace', 'content': compact})
            hashes[relative] = hashlib.sha256(raw).hexdigest()
    return {'schema': BUNDLE_SCHEMA, 'operation_id': operation_id,
            'operation_type': 'migration', 'expected_hashes': hashes, 'writes': writes}
