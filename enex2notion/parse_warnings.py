"""Centralized warning collection for format transformations and limitations.

Tracks all material format changes, truncations, and limitations during
note parsing and conversion to help users understand what was transformed.
"""
import threading
from typing import Optional

# Thread-local storage for warnings during parsing
_warnings_context = threading.local()


def init_warnings():
    """Initialize warnings collection for current thread."""
    _warnings_context.warnings = []


def add_warning(message: str):
    """Add a warning about format transformation or limitation.
    
    Args:
        message: Warning message describing the transformation
    """
    if not hasattr(_warnings_context, "warnings"):
        init_warnings()
    _warnings_context.warnings.append(message)


def get_warnings() -> list[str]:
    """Get all collected warnings for current thread.
    
    Returns:
        List of warning messages
    """
    if not hasattr(_warnings_context, "warnings"):
        return []
    return _warnings_context.warnings.copy()


def clear_warnings():
    """Clear all warnings for current thread."""
    if hasattr(_warnings_context, "warnings"):
        _warnings_context.warnings = []
