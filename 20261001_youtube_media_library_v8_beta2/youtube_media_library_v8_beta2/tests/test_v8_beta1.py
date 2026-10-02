import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from beta_helpers import app_env, USER, PASSWORD, LOCAL, PRIVATE
from yme.remote_security import password_record, Security, ServerConfig, set_owner


def test_password_minimum_is_eight():
    with pytest.raises(ValueError):
        password_record("1234567")
    assert password_record("12345678")["algorithm"] == "scrypt-v1"


def test_login_cookie_remember_me(tmp_path):
    app = app_env(tmp_path)
    c = TestClient(app, base_url=PRIVATE, client=("127.0.0.1", 53333))
    c.headers["Origin"] = PRIVATE
    r = c.post("/auth/login", json={"username": USER, "password": PASSWORD, "remember": True})
    assert r.status_code == 200 and r.json()["remembered"] is True
    assert "Max-Age=2592000" in r.headers["set-cookie"]
    with app.state.security.connect() as sql:
        row = sql.execute("SELECT remembered,expires,created FROM sessions").fetchone()
        assert row["remembered"] == 1
        assert 29*86400 < row["expires"] - row["created"] <= 30*86400 + 5


def test_normal_login_is_session_cookie(tmp_path):
    app = app_env(tmp_path)
    c = TestClient(app, base_url=LOCAL, client=("127.0.0.1", 53333))
    c.headers["Origin"] = LOCAL
    r = c.post("/auth/login", json={"username": USER, "password": PASSWORD, "remember": False})
    assert r.status_code == 200 and r.json()["remembered"] is False
    assert "Max-Age=" not in r.headers["set-cookie"]
    with app.state.security.connect() as sql:
        assert sql.execute("SELECT remembered FROM sessions").fetchone()["remembered"] == 0


def test_remembered_session_ignores_idle_but_obeys_absolute_expiry(tmp_path):
    app = app_env(tmp_path)
    c = TestClient(app, base_url=PRIVATE, client=("127.0.0.1", 53333))
    c.headers["Origin"] = PRIVATE
    assert c.post("/auth/login", json={"username": USER, "password": PASSWORD, "remember": True}).status_code == 200
    with app.state.security.connect() as sql:
        sql.execute("UPDATE sessions SET seen=?", (time.time()-10*86400,))
    assert c.get("/api/items").status_code == 200
    with app.state.security.connect() as sql:
        sql.execute("UPDATE sessions SET expires=?", (time.time()-1,))
    assert c.get("/api/items").status_code == 401


def test_old_security_db_migrates_remembered_column(tmp_path):
    folder = tmp_path/"config"; folder.mkdir()
    set_owner(folder, USER, PASSWORD)
    db = folder/"security.sqlite3"
    with sqlite3.connect(db) as c:
        c.executescript("""
        CREATE TABLE sessions (digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, origin TEXT NOT NULL,
          revision TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, seen REAL NOT NULL);
        CREATE TABLE attempts (at REAL NOT NULL);
        CREATE TABLE audit (at REAL NOT NULL, event TEXT NOT NULL, mode TEXT NOT NULL);
        """)
    sec = Security(ServerConfig(data_dir=str(tmp_path/"data"), output_dir=str(tmp_path/"output")), folder)
    with sec.connect() as c:
        cols = {row["name"] for row in c.execute("PRAGMA table_info(sessions)")}
    assert "remembered" in cols
