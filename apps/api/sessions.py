"""Process-local opaque sessions for explicitly configured synthetic seed accounts."""

import hashlib
import hmac
import json
import os
import secrets
import time
from collections import deque
from pathlib import Path

from fastapi import HTTPException

from packages.tools.schemas import AuthContext

COOKIE = "supportops_session"


class Sessions:
    def __init__(self, *, clock=time.monotonic, ttl=3600):
        self.clock, self.ttl = clock, ttl
        self.tokens = {}
        self.attempts = deque()
        self.subjects = {}
        root = Path(__file__).resolve().parents[2]
        for tenant in json.loads((root / "data/demo/tenants.json").read_text(encoding="utf-8")):
            for user in tenant["users"]:
                self.subjects[user["id"]] = AuthContext(
                    user_id=user["id"],
                    tenant_id=tenant["id"],
                    role=user["role"],
                    assigned_tenant_ids=(tenant["id"],) if user["role"] == "support" else (),
                )
        self.salt = secrets.token_bytes(32)
        self.passwords = {}
        raw = os.getenv("SUPPORTOPS_DEMO_PASSWORDS", "")
        if raw:
            try:
                config = json.loads(raw)
                if not isinstance(config, dict) or set(config) != set(self.subjects):
                    raise ValueError
                if any(not isinstance(p, str) or not 12 <= len(p) <= 256 for p in config.values()):
                    raise ValueError
                if len(set(config.values())) != len(config):
                    raise ValueError
                self.passwords = {u: self._hash(p) for u, p in config.items()}
            except (ValueError, TypeError):
                raise ValueError("Invalid SUPPORTOPS_DEMO_PASSWORDS configuration") from None
        self.dummy = self._hash(secrets.token_urlsafe(32))

    def _hash(self, password):
        return hashlib.pbkdf2_hmac("sha256", password.encode(), self.salt, 600_000)

    @staticmethod
    def _key(token):
        return hashlib.sha256(token.encode()).digest()

    def issue(self, user_id):
        """Trusted server operation; HTTP callers must first authenticate."""
        now = self.clock()
        self.tokens = {k: v for k, v in self.tokens.items() if v[1] > now}
        if len(self.tokens) >= 1024:
            raise HTTPException(503, "会话已满，请稍后重试。")
        subject = self.subjects[user_id]
        token = secrets.token_urlsafe(32)
        self.tokens[self._key(token)] = (subject, now + self.ttl)
        return token

    def login(self, username, password):
        if not self.passwords:
            raise HTTPException(503, "请先配置本机种子账号口令。")
        now = self.clock()
        while self.attempts and self.attempts[0] <= now - 60:
            self.attempts.popleft()
        if len(self.attempts) >= 20:
            raise HTTPException(429, "登录尝试过多，请稍后重试。")
        self.attempts.append(now)
        expected = self.passwords.get(username, self.dummy)
        valid = hmac.compare_digest(self._hash(password), expected)
        if not valid or username not in self.passwords:
            raise HTTPException(401, "账号或口令不正确。")
        return self.issue(username)

    def resolve(self, token):
        key = self._key(token or "")
        saved = self.tokens.get(key)
        if saved is None or saved[1] <= self.clock():
            self.tokens.pop(key, None)
            raise HTTPException(401, "请登录，或重新登录已过期的会话。")
        return saved[0]

    def revoke(self, token):
        self.tokens.pop(self._key(token or ""), None)
