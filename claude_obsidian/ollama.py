"""Shared proof that an Ollama model is installed and local-only."""

import re


MODEL_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?\Z"
)
DIGEST_RE = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")


class OllamaLocalProofError(ValueError):
    """The server response does not prove local-only model execution."""


class OllamaModelUnavailable(OllamaLocalProofError):
    """The requested model or its permitted alias is not installed."""


def _valid_model_name(model):
    if not isinstance(model, str) or MODEL_RE.fullmatch(model) is None:
        raise OllamaLocalProofError("model identity is malformed")
    repository = model.split(":", 1)[0]
    if any(part in {"", ".", ".."} for part in repository.split("/")):
        raise OllamaLocalProofError("model identity is malformed")
    return model


def _cloud_tag(model):
    return ":" in model and "cloud" in model.rsplit(":", 1)[1].lower()


def _remote_identity(value):
    if isinstance(value, dict):
        if any(
            value.get(key) not in (None, "")
            for key in ("remote_host", "remote_model")
        ):
            return True
        return any(_remote_identity(item) for item in value.values())
    if isinstance(value, list):
        return any(_remote_identity(item) for item in value)
    return False


def _valid_digest(value):
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


def normalize_model_digest(value):
    if not _valid_digest(value):
        raise OllamaLocalProofError("installed model digest is unavailable")
    return value if value.startswith("sha256:") else f"sha256:{value}"


def _aliases(model):
    return (model,) if ":" in model else (model, f"{model}:latest")


def _matching_entries(model, models):
    _valid_model_name(model)
    if _cloud_tag(model):
        raise OllamaLocalProofError("cloud model tags are not local-only")
    if not isinstance(models, list):
        raise OllamaLocalProofError("server model list is malformed")
    aliases = _aliases(model)
    matches = []
    for entry in models:
        if not isinstance(entry, dict):
            raise OllamaLocalProofError("server model entry is malformed")
        name = entry.get("name")
        if name not in aliases:
            continue
        if not isinstance(name, str) or _cloud_tag(name):
            raise OllamaLocalProofError("cloud model tags are not local-only")
        if entry.get("model") not in (None, "", name):
            raise OllamaLocalProofError("installed model identity conflicts")
        if _remote_identity(entry):
            raise OllamaLocalProofError("installed model has remote identity")
        digest = normalize_model_digest(entry.get("digest"))
        matches.append((name, digest))
    if not matches:
        raise OllamaModelUnavailable("requested model is not installed")
    if len({digest for _name, digest in matches}) != 1:
        raise OllamaLocalProofError("installed model aliases have conflicting digests")
    preferred = next((item for item in matches if item[0] == model), matches[0])
    return preferred


def model_is_available(model, models):
    """Return whether tags contain one exact permitted identity with valid proof."""
    try:
        _matching_entries(model, models)
    except OllamaLocalProofError:
        return False
    return True


def prove_local_model(model, get_json, post_json, *, require_explicit_tag=False):
    """Return resolved model identity and digest after local-only preflight."""
    _valid_model_name(model)
    if require_explicit_tag and ":" not in model:
        raise OllamaLocalProofError("an explicit model tag is required")
    status = get_json("status")
    if (
        not isinstance(status, dict)
        or _remote_identity(status)
        or not isinstance(status.get("cloud"), dict)
        or status["cloud"].get("disabled") is not True
    ):
        raise OllamaLocalProofError("server does not prove cloud is disabled")
    tags = get_json("tags")
    if not isinstance(tags, dict) or _remote_identity(tags):
        raise OllamaLocalProofError("server model list is malformed")
    resolved_model, digest = _matching_entries(model, tags.get("models"))
    shown = post_json("show", {"model": resolved_model})
    if not isinstance(shown, dict) or _remote_identity(shown):
        raise OllamaLocalProofError("model metadata does not prove a local model")
    model_info = shown.get("model_info")
    if not isinstance(model_info, dict) or not model_info:
        raise OllamaLocalProofError("model metadata does not prove a local model")
    for key in ("model", "name"):
        if shown.get(key) not in (None, "", resolved_model):
            raise OllamaLocalProofError("model metadata identity conflicts")
    shown_digest = shown.get("digest")
    if shown_digest not in (None, ""):
        if normalize_model_digest(shown_digest) != digest:
            raise OllamaLocalProofError("model metadata digest conflicts")
    return {"model": resolved_model, "digest": digest}


def validate_model_response(response, proof):
    """Reject remote markers or a model identity that conflicts with proof."""
    if not isinstance(response, dict) or _remote_identity(response):
        raise OllamaLocalProofError("provider response has remote identity")
    model = proof.get("model") if isinstance(proof, dict) else None
    raw_digest = proof.get("digest") if isinstance(proof, dict) else None
    if not isinstance(model, str) or not _valid_digest(raw_digest):
        raise OllamaLocalProofError("verified model identity is malformed")
    digest = normalize_model_digest(raw_digest)
    for key in ("model", "name"):
        if response.get(key) not in (None, "", model):
            raise OllamaLocalProofError("provider response model identity conflicts")
    returned_digest = response.get("digest")
    if returned_digest not in (None, ""):
        if normalize_model_digest(returned_digest) != digest:
            raise OllamaLocalProofError("provider response digest conflicts")
    return response
