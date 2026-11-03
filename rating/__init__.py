"""Outcome-only rating v2; no AWS or Discord dependencies in the model."""
import os


def enabled():
    mode = os.environ.get("RATING_SYSTEM", "legacy")
    if mode not in ("legacy", "v2"):
        raise ValueError("RATING_SYSTEM must be legacy or v2")
    return mode == "v2"
