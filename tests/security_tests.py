#!/usr/bin/env python3
"""
security_tests.py
-----------------
Attacks the RUNNING backend to show each security control is enforced
(Chapter 5, Table 5.4). Start the backend first (python run.py), then in a
second terminal, from the zimlegal-backend folder:

    python tests/security_tests.py

It registers (or logs in) a test user tendai@example.com and posts one forum
post containing <script> tags (S7) -- run it on a test copy, not live data.
"""
import base64, hashlib, hmac, json, os, sqlite3, sys, time
import urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import SECRET_KEY, DATABASE_PATH

BASE = "http://localhost:8000"
EMAIL, PASSWORD = "tendai@example.com", "Harare2026"


def call(method, path, body=None, token=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def show(test_id, desc, result):
    print(f"{test_id} | {desc} | {result[0]} | {json.dumps(result[1])[:110]}")


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


_, reg = call("POST", "/auth/register", {"full_name": "Tendai Moyo", "email": EMAIL, "password": PASSWORD})
token = reg.get("access_token") or call("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})[1]["access_token"]

show("S1", "Register with 3-char password", call("POST", "/auth/register", {"full_name": "A B", "email": "a@b.com", "password": "abc"}))
show("S2", "SQL injection in login email", call("POST", "/auth/login", {"email": "' OR '1'='1' --", "password": "x"}))
show("S3", "Wrong password", call("POST", "/auth/login", {"email": EMAIL, "password": "wrong"}))
show("S4", "Forum post with no token", call("POST", "/forum/posts", {"title": "t", "body": "b"}))

payload_b64, signature = token.split(".")
payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
payload["user_id"] = 999
forged = b64(json.dumps(payload).encode()) + "." + signature
show("S5", "Tampered token (user_id changed)", call("POST", "/forum/posts", {"title": "t", "body": "b"}, token=forged))

expired = b64(json.dumps({"user_id": 1, "email": EMAIL, "exp": int(time.time()) - 60}).encode())
expired += "." + b64(hmac.new(SECRET_KEY.encode(), expired.encode(), hashlib.sha256).digest())
show("S6", "Expired token (validly signed)", call("POST", "/forum/posts", {"title": "t", "body": "b"}, token=expired))

show("S7", "Forum post containing <script> (valid token)", call("POST", "/forum/posts", {
    "title": "<script>alert('hacked')</script> Help with summons",
    "body": "<img src=x onerror=alert(1)> How do I serve summons?"}, token=token))
show("S8", "Dictionary look-up of <script>", call("GET", "/dictionary?q=%3Cscript%3E"))
show("S9", "Malformed JSON body", call("POST", "/auth/login", raw=b"{not json"))

row = sqlite3.connect(DATABASE_PATH).execute(
    "SELECT email, password_hash, salt FROM users WHERE email = ?", (EMAIL,)).fetchone()
print(f"S10 | stored row | {row[0]} | hash: {row[1][:32]}... | salt: {row[2]}")

codes = {call("POST", "/auth/login", {"email": EMAIL, "password": f"guess{i}"})[0] for i in range(20)}
print(f"S11 | 20 wrong passwords in a row | codes: {codes} | then correct: "
      f"{call('POST', '/auth/login', {'email': EMAIL, 'password': PASSWORD})[0]}")
