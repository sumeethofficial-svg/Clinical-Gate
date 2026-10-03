"""Password hashing (PBKDF2-SHA256, stdlib only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

ITERATIONS = int(os.environ.get("PASSWORD_HASH_ITERATIONS", "200000"))


def hash_password(password: str, iterations: int | None = None) -> str:
    iters = iterations or ITERATIONS
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
    return f"pbkdf2_sha256${iters}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False
