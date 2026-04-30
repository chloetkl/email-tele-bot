from __future__ import annotations

import re

# Pragmatic (not RFC-perfect) email regex. Good enough for user input validation.
EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)


def is_valid_email(value: str) -> bool:
    value = (value or "").strip()
    return bool(EMAIL_RE.match(value))


def is_gmail_address(value: str) -> bool:
    value = (value or "").strip()
    return is_valid_email(value) and value.lower().endswith("@gmail.com")
