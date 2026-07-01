"""Route tests for manual entry (admin) — uses the Flask test client."""

import app as flask_app


def _client(data_dir, monkeypatch, admin=True):
    monkeypatch.setattr(flask_app, "ADMIN_OPEN", admin)  # trusted-local admin
    monkeypatch.setattr(flask_app, "ADMIN_PASSWORD", "")
    monkeypatch.setattr(flask_app, "BOOK_PASSWORD", "")  # no site auth in tests
    return flask_app.app.test_client()


def test_register_manual_without_isbn_gets_local_key(data_dir, monkeypatch):
    client = _client(data_dir, monkeypatch)
    resp = client.post("/api/register-manual",
                       json={"title": "Handwritten notes", "author": "Anon", "total_count": 2})
    assert resp.status_code == 200
    book = resp.get_json()["book"]
    assert book["isbn"].startswith("NOISBN-")
    assert book["title"] == "Handwritten notes"
    assert book["total_count"] == 2 and book["source"] == "MANUAL"


def test_register_manual_with_isbn_is_normalized(data_dir, monkeypatch):
    client = _client(data_dir, monkeypatch)
    resp = client.post("/api/register-manual",
                       json={"title": "With ISBN", "isbn": "978-0-13-110362-7"})
    assert resp.get_json()["book"]["isbn"] == "9780131103627"


def test_register_manual_requires_title(data_dir, monkeypatch):
    client = _client(data_dir, monkeypatch)
    assert client.post("/api/register-manual", json={"author": "x"}).status_code == 400


def test_register_manual_requires_admin(data_dir, monkeypatch):
    client = _client(data_dir, monkeypatch, admin=False)
    assert client.post("/api/register-manual", json={"title": "x"}).status_code == 403
