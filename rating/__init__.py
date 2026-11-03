"""The skill model replaces accumulated SR for all production rating paths."""
import os


def enabled():
    """Compatibility entry point, not a selectable feature flag.

    Old v2 configuration is accepted so an existing skill snapshot keeps working.
    Legacy formulas remain as historical reference code but cannot be selected
    at runtime. Rollback requires restoring the previous application release.
    """
    if os.environ.get("RATING_SYSTEM") not in (None, "", "v2"):
        raise ValueError(
            "Legacy SR has been replaced. Remove RATING_SYSTEM; "
            "the skill rating system is required. Rollback requires the previous release."
        )
    return True
