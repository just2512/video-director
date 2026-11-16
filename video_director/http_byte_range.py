"""Strict, bounded parsing for single HTTP byte-range requests.

Unsupported or malformed Range fields are ignored and served as a normal 200
response. A syntactically valid but unsatisfiable single range raises
UnsatisfiableByteRange so callers can return 416 with the complete length.
"""
from __future__ import annotations

import re


_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)", re.IGNORECASE)


class UnsatisfiableByteRange(ValueError):
    """A valid single byte range cannot select any byte in the representation."""


def parse_byte_range(range_header: str | None, total: int) -> tuple[int, int] | None:
    """Return inclusive (start, end), or None when Range should be ignored.

    This endpoint deliberately implements one byte range only. Multi-range,
    unknown-unit, and malformed requests fall back to a complete 200 response
    rather than returning a misleading 416 or dropping the connection.
    """
    total = int(total)
    if total < 0:
        raise ValueError("representation length cannot be negative")
    if not range_header:
        return None

    value = str(range_header).strip()
    match = _RANGE_RE.fullmatch(value)
    if match is None:
        return None

    first, last = match.groups()
    if not first and not last:
        return None

    try:
        if not first:
            suffix_length = int(last)
            if suffix_length <= 0:
                raise UnsatisfiableByteRange("zero-length suffix range")
            if total == 0:
                raise UnsatisfiableByteRange("empty representation")
            start = max(0, total - suffix_length)
            end = total - 1
        else:
            start = int(first)
            end = int(last) if last else total - 1
            if total == 0 or start >= total or end < start:
                raise UnsatisfiableByteRange("requested byte range is outside the representation")
            end = min(end, total - 1)
    except UnsatisfiableByteRange:
        raise
    except ValueError:
        # Includes pathological integer strings rejected by Python's safety limit.
        return None

    return start, end
