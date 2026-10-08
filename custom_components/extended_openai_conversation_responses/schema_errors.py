"""Validation exceptions used by supported Home Assistant schema engines."""

import voluptuous as vol

try:
    from probatio.error import Invalid as ProbatioInvalid
except ImportError:  # pragma: no cover - older Home Assistant releases
    SCHEMA_ERRORS: tuple[type[Exception], ...] = (vol.Invalid,)
else:
    SCHEMA_ERRORS = (vol.Invalid, ProbatioInvalid)
