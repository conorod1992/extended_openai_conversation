"""Exact candidate and environment identity checks shared by certification gates."""

from __future__ import annotations

import re

try:
    from .enhanced_evidence import environment_fingerprint
except ImportError:
    from enhanced_evidence import environment_fingerprint


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def check_candidate(item, candidate_sha, *, execution=True):
    label = item.get("campaign", "evidence")
    errors = []
    if not valid_sha(candidate_sha):
        return [f"Invalid intended candidate SHA: {candidate_sha!r}"]
    envelopes = [item]
    runs = item.get("execution_runs", [])
    if execution:
        if not runs:
            errors.append(f"{label}: missing source-bound execution ledgers")
        envelopes += runs
        ids = [run.get("execution_id") for run in runs]
        if not all(ids) or len(ids) != len(set(ids)):
            errors.append(f"{label}: missing/duplicate execution identity")
        for case in item.get("execution_cases", []):
            if case.get("execution_id") not in ids:
                errors.append(
                    f"{label}: {case['nodeid']} has no source-bound execution ledger"
                )
    for envelope in envelopes:
        tested = envelope.get("eoai_sha")
        if tested != candidate_sha:
            errors.append(
                f"{label}: intended candidate {candidate_sha}, tested SHA {tested}"
            )
        identity = envelope.get("environment")
        if (
            not isinstance(identity, dict)
            or not identity
            or envelope.get("environment_fingerprint")
            != environment_fingerprint(identity)
        ):
            errors.append(f"{label}: missing/inconsistent environment fingerprint")
    return errors
