"""
auth.py
-------
Two jobs live here:

1. Password hashing -- we never store a user's actual password. Instead we
   store a "hash" of it (a one-way scramble) plus a random "salt". To check
   a login attempt, we hash the *attempt* with the same salt and compare.
   We use `hashlib.pbkdf2_hmac`, which is built into Python and designed
   specifically for password hashing (it deliberately runs slowly, which
   makes guessing passwords by brute force impractical).

2. Login tokens -- after a successful login/signup we hand the frontend a
   token it can send back on future requests (in the `Authorization: Bearer
   <token>` header) to prove who it is, without sending the password again
   every time. This is the same idea as a JWT (JSON Web Token), implemented
   here by hand with `hmac` + `hashlib` so we don't need any third-party
   library. A token has two parts separated by a dot:

       <base64 of the payload JSON>.<base64 of an HMAC signature>

   The signature is computed from the payload using our secret key. Anyone
   can read the payload (it's just base64, not encryption), but nobody can
   *change* it without knowing the secret key, because the signature
   wouldn't match anymore. That's what makes the token trustworthy.
"""

import hashlib
import hmac
import os
import json
import base64
import time

from app.config import SECRET_KEY, TOKEN_LIFETIME_SECONDS

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 200_000  # higher = slower to brute-force, slower to check


def hash_password(password, salt=None):
    """Returns (hash_hex, salt_hex). Pass an existing salt_hex to check a
    password against a previously stored hash.
    """
    if salt is None:
        salt = os.urandom(16).hex()
    salt_bytes = bytes.fromhex(salt)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, _PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password, stored_hash_hex, salt_hex):
    computed_hash, _ = hash_password(password, salt_hex)
    # hmac.compare_digest avoids leaking timing information about how much
    # of the hash matched, which is safer than using `==`.
    return hmac.compare_digest(computed_hash, stored_hash_hex)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def _b64encode(data_bytes):
    return base64.urlsafe_b64encode(data_bytes).rstrip(b"=").decode("ascii")


def _b64decode(data_str):
    padding = "=" * (-len(data_str) % 4)
    return base64.urlsafe_b64decode(data_str + padding)


def create_token(user_id, email):
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": int(time.time()) + TOKEN_LIFETIME_SECONDS,
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    payload_b64 = _b64encode(payload_bytes)

    signature = hmac.new(SECRET_KEY.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    signature_b64 = _b64encode(signature)

    return f"{payload_b64}.{signature_b64}"


def verify_token(token):
    """Returns the decoded payload dict if the token is valid and not
    expired, otherwise returns None.
    """
    if not token or "." not in token:
        return None

    payload_b64, _, signature_b64 = token.partition(".")

    expected_signature = hmac.new(
        SECRET_KEY.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    expected_signature_b64 = _b64encode(expected_signature)

    if not hmac.compare_digest(expected_signature_b64, signature_b64):
        return None  # signature doesn't match -- token was tampered with, or signed with a different key

    try:
        payload = json.loads(_b64decode(payload_b64))
    except Exception:
        return None

    if payload.get("exp", 0) < time.time():
        return None  # token has expired

    return payload
