#!/usr/bin/env python3
"""Hermetic tests for the shared Ollama local-model proof."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_obsidian.ollama import (
    OllamaLocalProofError,
    OllamaModelUnavailable,
    model_is_available,
    prove_local_model,
    validate_model_response,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def proof_responses(*, status=None, models=None, show=None):
    documents = {
        "status": status if status is not None else {"cloud": {"disabled": True}},
        "tags": {"models": models if models is not None else [
            {"name": "fixture", "digest": DIGEST_A},
        ]},
    }
    shown = show if show is not None else {"model_info": {"general.architecture": "fixture"}}
    events = []

    def get_json(endpoint):
        events.append(("get", endpoint))
        return documents[endpoint]

    def post_json(endpoint, payload):
        events.append(("post", endpoint, payload))
        return shown

    return get_json, post_json, events


class LocalModelProofTests(unittest.TestCase):
    def test_status_must_prove_cloud_disabled_with_exact_boolean_true(self):
        for status in (
            {},
            {"cloud": {}},
            {"cloud": {"disabled": False}},
            {"cloud": {"disabled": 1}},
            {"cloud": {"disabled": "true"}},
        ):
            get_json, post_json, events = proof_responses(status=status)
            with self.assertRaises(OllamaLocalProofError):
                prove_local_model("fixture", get_json, post_json)
            self.assertEqual([("get", "status")], events)

    def test_untagged_prefers_exact_and_allows_only_latest_alias(self):
        models = [
            {"name": "fixture:latest", "digest": DIGEST_A},
            {"name": "fixture", "digest": DIGEST_A},
            {"name": "fixture:other", "digest": DIGEST_B},
        ]
        get_json, post_json, events = proof_responses(models=models)
        proof = prove_local_model("fixture", get_json, post_json)
        self.assertEqual("fixture", proof["model"])
        self.assertEqual(DIGEST_A, proof["digest"])
        self.assertEqual(("post", "show", {"model": "fixture"}), events[-1])
        self.assertTrue(model_is_available("fixture", models))
        self.assertFalse(model_is_available("fixture:missing", models))

    def test_untagged_aliases_with_conflicting_digests_are_rejected(self):
        get_json, post_json, _events = proof_responses(models=[
            {"name": "fixture", "digest": DIGEST_A},
            {"name": "fixture:latest", "digest": DIGEST_B},
        ])
        with self.assertRaisesRegex(OllamaLocalProofError, "digest"):
            prove_local_model("fixture", get_json, post_json)

    def test_digest_prefix_spelling_is_normalized_before_alias_comparison(self):
        bare = "a" * 64
        get_json, post_json, _events = proof_responses(
            models=[
                {"name": "fixture", "digest": bare},
                {"name": "fixture:latest", "digest": "sha256:" + bare},
            ],
            show={
                "model_info": {"general.architecture": "fixture"},
                "digest": bare,
            },
        )
        proof = prove_local_model("fixture", get_json, post_json)
        self.assertEqual("sha256:" + bare, proof["digest"])

    def test_explicit_tag_requires_exact_identity(self):
        get_json, post_json, _events = proof_responses(models=[
            {"name": "fixture:latest", "digest": DIGEST_A},
        ])
        proof = prove_local_model("fixture:latest", get_json, post_json)
        self.assertEqual("fixture:latest", proof["model"])
        with self.assertRaises(OllamaModelUnavailable):
            prove_local_model("fixture:other", get_json, post_json)
        with self.assertRaises(OllamaLocalProofError):
            prove_local_model("fixture", get_json, post_json, require_explicit_tag=True)

    def test_cloud_tags_remote_metadata_and_bad_digests_are_rejected(self):
        cases = (
            [{"name": "fixture:cloud", "digest": DIGEST_A}],
            [{"name": "fixture", "digest": "bad"}],
            [{"name": "fixture", "digest": DIGEST_A, "remote_host": "https://example.test"}],
            [{"name": "fixture", "digest": DIGEST_A, "remote_model": "remote:tag"}],
            [{"name": "fixture", "model": "other", "digest": DIGEST_A}],
        )
        requested = ("fixture:cloud", "fixture", "fixture", "fixture", "fixture")
        for model, models in zip(requested, cases):
            get_json, post_json, _events = proof_responses(models=models)
            with self.assertRaises(OllamaLocalProofError):
                prove_local_model(model, get_json, post_json)

    def test_show_requires_local_metadata_and_consistent_identity(self):
        cases = (
            {},
            {"model_info": {}},
            {"model_info": {"x": 1}, "remote_host": "https://example.test"},
            {"model_info": {"x": 1}, "remote_model": "remote:tag"},
            {"model_info": {"x": 1}, "model": "other"},
            {"model_info": {"x": 1}, "digest": DIGEST_B},
        )
        for show in cases:
            get_json, post_json, _events = proof_responses(show=show)
            with self.assertRaises(OllamaLocalProofError):
                prove_local_model("fixture", get_json, post_json)

    def test_embedding_response_rejects_remote_or_conflicting_identity(self):
        proof = {"model": "fixture", "digest": DIGEST_A}
        validate_model_response({}, proof)
        validate_model_response({"model": "fixture", "digest": DIGEST_A}, proof)
        for response in (
            {"remote_host": "https://example.test"},
            {"remote_model": "remote:tag"},
            {"model": "other"},
            {"name": "other"},
            {"digest": DIGEST_B},
        ):
            with self.assertRaises(OllamaLocalProofError):
                validate_model_response(response, proof)


if __name__ == "__main__":
    unittest.main(verbosity=2)
