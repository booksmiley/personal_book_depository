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


def test_import_csv_adds_updates_and_skips(data_dir, monkeypatch):
    import io

    client = _client(data_dir, monkeypatch)
    client.post("/api/register-manual", json={"title": "Old", "isbn": "9780131103627"})
    csv = (
        "isbn,title,author,total_count\n"
        "9780131103627,New Title,K&R,3\n"   # update existing
        ",Journal,Anon,1\n"                 # add (no ISBN -> local key)
        ",,skip me,\n"                      # skipped (no title)
    )
    resp = client.post(
        "/api/import.csv",
        data={"file": (io.BytesIO(csv.encode()), "lib.csv")},
        content_type="multipart/form-data",
    )
    assert resp.get_json() == {
        "status": "imported", "added": 1, "updated": 1, "skipped": 1,
    }


def test_import_csv_requires_admin(data_dir, monkeypatch):
    import io

    client = _client(data_dir, monkeypatch, admin=False)
    resp = client.post(
        "/api/import.csv",
        data={"file": (io.BytesIO(b"isbn,title\n"), "x.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 403
