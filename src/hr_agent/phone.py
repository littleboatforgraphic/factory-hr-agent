"""Phone number normalisation shared by the factory node and the chat channels."""

from __future__ import annotations

import re


def normalize_phone(raw: str | None, default_country: str = "60") -> str:
    """Digits only, in international form without '+': '012-345 6789' -> '60123456789'."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_country + digits[1:]
    return digits
