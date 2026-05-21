"""Seed demo users on first launch (doc §9.1 -- viewer/editor/admin)."""
from __future__ import annotations

import logging

from sqlalchemy.orm import sessionmaker

from .auth.security import hash_password
from .db.models import User

log = logging.getLogger("quant_web.seed")

# (username, password, role, nickname, email)
DEFAULT_USERS = [
    ("admin", "admin123", "admin", "维林", "weilin@zj-quant.com"),
    ("editor", "editor123", "editor", "李珺", "lijun@zj-quant.com"),
    ("viewer", "viewer123", "viewer", "访客", "viewer@zj-quant.com"),
]


def ensure_seed_users(session_factory: sessionmaker) -> None:
    with session_factory() as db:
        if db.query(User).count() > 0:
            return
        for username, password, role, nickname, email in DEFAULT_USERS:
            db.add(User(username=username, password_hash=hash_password(password),
                        role=role, nickname=nickname, email=email, is_active=True))
        db.commit()
        # never log plaintext passwords; just record which usernames/roles were created
        log.warning("seeded %d demo users: %s",
                    len(DEFAULT_USERS),
                    ", ".join(f"{u}({r})" for u, _pw, r, _n, _e in DEFAULT_USERS))
