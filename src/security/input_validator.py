from __future__ import annotations

import re
from typing import Any

DANGEROUS_PATTERNS = [
    r"<script[^>]*>.*?</script>",
    r"javascript\s*:",
    r"on\w+\s*=",
    r"eval\s*\(",
    r"expression\s*\(",
    r"vbscript\s*:",
    r"data\s*:",
]

SQL_INJECTION_PATTERNS = [
    r"(?i)(\b(union\s+select|select\s+.+\s+from|insert\s+into|delete\s+from|drop\s+table|alter\s+table|exec\s*\(|execute\s*\()\b)",
    r"(?i)(\b(or|and)\s+\d+\s*=\s*\d+)",
    r"(?i)(\b(or|and)\s+['\"]?\w+['\"]?\s*=\s*['\"]?\w+['\"]?)",
    r"--\s*$",
    r"/\*.*\*/",
    r"(?i)\bwaitfor\s+delay\b",
]

PATH_TRAVERSAL_PATTERNS = [
    r"\.\./",
    r"\.\.\\",
    r"%2e%2e%2f",
    r"%2e%2e/",
    r"\.\.%2f",
]


def sanitize_string(value: str, max_length: int = 10000) -> str:
    if not isinstance(value, str):
        return str(value)
    result = value[:max_length]
    for pattern in DANGEROUS_PATTERNS:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE | re.DOTALL)
    return result.strip()


def validate_no_sql_injection(value: str) -> bool:
    if not isinstance(value, str):
        return True
    return all(not re.search(pattern, value, re.IGNORECASE) for pattern in SQL_INJECTION_PATTERNS)


def validate_no_path_traversal(value: str) -> bool:
    if not isinstance(value, str):
        return True
    return all(not re.search(pattern, value, re.IGNORECASE) for pattern in PATH_TRAVERSAL_PATTERNS)


def validate_input(data: Any, max_depth: int = 5, max_keys: int = 50) -> list[str]:
    errors: list[str] = []

    if isinstance(data, dict):
        if len(data) > max_keys:
            errors.append(f"Too many keys: {len(data)} > {max_keys}")
        for key, value in data.items():
            if not isinstance(key, str):
                errors.append(f"Non-string key: {key}")
            elif len(key) > 256:
                errors.append(f"Key too long: {key[:50]}...")
            if max_depth > 0:
                child_errors = validate_input(value, max_depth - 1, max_keys)
                errors.extend(child_errors)
    elif isinstance(data, list):
        if len(data) > 1000:
            errors.append(f"List too long: {len(data)} > 1000")
        for item in data[:10]:
            if max_depth > 0:
                child_errors = validate_input(item, max_depth - 1, max_keys)
                errors.extend(child_errors)
    elif isinstance(data, str):
        if len(data) > 100000:
            errors.append(f"String too long: {len(data)} > 100000")
        if not validate_no_sql_injection(data):
            errors.append("Potential SQL injection detected")
        if not validate_no_path_traversal(data):
            errors.append("Potential path traversal detected")

    return errors
