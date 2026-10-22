"""Shared byte envelopes without importing the mutation engine."""

import math

# The hot cache is read in full at the start of every session, so its budget is
# a reading-cost cap denominated in tokens (see skills/save/SKILL.md). Claude's
# tokenizer is not published as a library, and lint and the workflow gate must
# stay deterministic, offline and dependency-free, so this is a calibrated
# estimate rather than a true count.
HOT_CACHE_TOKEN_BUDGET = 2048
# Chars per token for plain ASCII prose; the usual English BPE ratio.
_TOK_ASCII_CHARS = 4.0
# Accented Latin, Cyrillic and similar BMP letters split far worse than ASCII.
_TOK_LATIN_EXT_CHARS = 1.5
# Emoji, arrows and other symbols above U+2000, often with variation selectors.
_TOK_SYMBOL_TOKENS = 2.0


def estimate_tokens(text: str) -> int:
    """Estimate token count without a tokenizer.

    Accurate to roughly +/-15% on wiki prose, which is enough for a soft cap:
    the number only has to distinguish "near budget" from "wildly over". On a
    840-word hot cache this returns ~1365 against a naive chars/4 of ~1284.
    """
    ascii_chars = 0
    latin_ext_chars = 0
    symbol_chars = 0
    for char in text:
        code = ord(char)
        if code < 128:
            ascii_chars += 1
        elif code <= 0x2000:
            latin_ext_chars += 1
        else:
            symbol_chars += 1
    return math.ceil(
        ascii_chars / _TOK_ASCII_CHARS
        + latin_ext_chars / _TOK_LATIN_EXT_CHARS
        + symbol_chars * _TOK_SYMBOL_TOKENS
    )


MAX_TRANSACTION_RUNTIME_JSON_BYTES = 8 * 1024 * 1024
MAX_CANONICAL_LEDGER_BYTES = 16 * 1024 * 1024
CANONICAL_LEDGER_PATHS = (
    "wiki/meta/ledgers/source-ledger.json",
    "wiki/meta/ledgers/claim-ledger.json",
)


def ledger_capacity_message(relative: str, size: int | str) -> str:
    return (
        f"{relative} has {size} bytes; canonical ledger capacity is "
        f"{MAX_CANONICAL_LEDGER_BYTES} bytes. Preserve the original bytes and "
        "prepare a reviewed capacity compaction or migration. Existing "
        "transaction backups remain recoverable within their original bounds."
    )
