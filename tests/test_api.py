import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated client with a fresh DB and FAISS dir per test."""
    import RAG_Frontend
    import RAG_Backend

    monkeypatch.setattr(RAG_Frontend, "DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setattr(RAG_Backend, "INDEX_BASE_DIR", str(tmp_path / "faiss"))
    os.makedirs(tmp_path / "faiss", exist_ok=True)
    RAG_Frontend.init_db()

    with TestClient(RAG_Frontend.app) as c:
        yield c


# ── Unit: make_doc_id ─────────────────────────────────────────────────────────

class TestMakeDocId:
    def test_same_content_gives_same_id(self):
        from RAG_Backend import make_doc_id
        assert make_doc_id(b"hello world") == make_doc_id(b"hello world")

    def test_different_content_gives_different_ids(self):
        from RAG_Backend import make_doc_id
        assert make_doc_id(b"content a") != make_doc_id(b"content b")

    def test_id_is_12_chars(self):
        from RAG_Backend import make_doc_id
        assert len(make_doc_id(b"anything")) == 12


# ── Documents ─────────────────────────────────────────────────────────────────

class TestDocuments:
    def test_list_empty(self, client):
        r = client.get("/documents")
        assert r.status_code == 200
        assert r.json() == {}

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/documents/doesnotexist")
        assert r.status_code == 404

    @patch("RAG_Frontend.index_document")
    def test_upload_registers_document(self, mock_index, client):
        mock_index.return_value = None
        r = client.post("/upload", files={"file": ("notes.txt", b"hello world", "text/plain")})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "success"
        assert "doc_id" in data
        docs = client.get("/documents").json()
        assert data["doc_id"] in docs
        assert docs[data["doc_id"]]["filename"] == "notes.txt"

    @patch("RAG_Frontend.index_document")
    def test_upload_same_content_deduplicates(self, mock_index, client):
        mock_index.return_value = None
        client.post("/upload", files={"file": ("a.txt", b"identical content", "text/plain")})
        client.post("/upload", files={"file": ("b.txt", b"identical content", "text/plain")})
        docs = client.get("/documents").json()
        assert len(docs) == 1  # same content → same doc_id → one entry

    @patch("RAG_Frontend.index_document")
    def test_upload_same_name_different_content_creates_two_docs(self, mock_index, client):
        mock_index.return_value = None
        client.post("/upload", files={"file": ("report.txt", b"version 1", "text/plain")})
        client.post("/upload", files={"file": ("report.txt", b"version 2", "text/plain")})
        docs = client.get("/documents").json()
        assert len(docs) == 2  # different content → different doc_ids, no silent overwrite

    @patch("RAG_Frontend.index_document")
    def test_upload_then_delete_removes_from_list(self, mock_index, client):
        mock_index.return_value = None
        doc_id = client.post(
            "/upload", files={"file": ("del.txt", b"data", "text/plain")}
        ).json()["doc_id"]
        assert client.delete(f"/documents/{doc_id}").status_code == 200
        assert client.get("/documents").json() == {}


# ── Conversations ─────────────────────────────────────────────────────────────

class TestConversations:
    def test_list_empty(self, client):
        r = client.get("/conversations")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_returns_conv_id_and_title(self, client):
        r = client.post("/conversations", json={"title": "Hello"})
        assert r.status_code == 200
        data = r.json()
        assert "conv_id" in data
        assert data["title"] == "Hello"

    def test_create_empty_title(self, client):
        r = client.post("/conversations", json={})
        assert r.status_code == 200
        assert r.json()["title"] == ""

    def test_list_after_create(self, client):
        client.post("/conversations", json={"title": "A"})
        client.post("/conversations", json={"title": "B"})
        r = client.get("/conversations")
        titles = [c["title"] for c in r.json()]
        assert titles == ["A", "B"]

    def test_update_title(self, client):
        conv_id = client.post("/conversations", json={"title": ""}).json()["conv_id"]
        client.patch(f"/conversations/{conv_id}", json={"title": "Updated"})
        r = client.get("/conversations")
        assert r.json()[0]["title"] == "Updated"

    def test_delete_conversation(self, client):
        conv_id = client.post("/conversations", json={"title": "Temp"}).json()["conv_id"]
        r = client.delete(f"/conversations/{conv_id}")
        assert r.status_code == 200
        assert client.get("/conversations").json() == []

    def test_clear_all(self, client):
        client.post("/conversations", json={"title": "A"})
        client.post("/conversations", json={"title": "B"})
        r = client.delete("/conversations")
        assert r.status_code == 200
        assert client.get("/conversations").json() == []

    def test_messages_empty_for_new_conversation(self, client):
        conv_id = client.post("/conversations", json={"title": ""}).json()["conv_id"]
        r = client.get(f"/conversations/{conv_id}/messages")
        assert r.status_code == 200
        assert r.json() == []

    def test_delete_cascades_to_messages(self, client):
        """Deleting a conversation must remove its messages (FK cascade)."""
        import RAG_Frontend
        conv_id = client.post("/conversations", json={"title": "X"}).json()["conv_id"]

        with RAG_Frontend.get_db() as conn:
            conn.execute(
                "INSERT INTO messages (conv_id, role, content) VALUES (?, ?, ?)",
                (conv_id, "user", "test message")
            )

        client.delete(f"/conversations/{conv_id}")

        with RAG_Frontend.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conv_id = ?", (conv_id,)
            ).fetchone()[0]
        assert count == 0


# ── Chat ──────────────────────────────────────────────────────────────────────

def _fake_stream_side_effect(chunks):
    """Returns a mock for get_response_stream that yields the given text chunks."""
    async def inner(*args, **kwargs):
        async def gen():
            for text in chunks:
                chunk = MagicMock()
                chunk.content = text
                yield chunk
        return gen()
    return inner


class TestChat:
    def _make_conv_and_doc(self, client):
        import RAG_Frontend
        conv_id = client.post("/conversations", json={}).json()["conv_id"]
        with RAG_Frontend.get_db() as conn:
            conn.execute(
                "INSERT INTO documents (doc_id, filename) VALUES (?, ?)",
                ("testdoc", "sample.txt")
            )
        return conv_id

    def test_doc_without_faiss_index_returns_not_found(self, client):
        """Doc registered in DB but FAISS index missing → graceful error message."""
        import RAG_Frontend
        conv_id = client.post("/conversations", json={}).json()["conv_id"]
        with RAG_Frontend.get_db() as conn:
            conn.execute(
                "INSERT INTO documents (doc_id, filename) VALUES (?, ?)",
                ("ghost", "deleted.txt")
            )
        r = client.post("/chat", data={"question": "hi", "doc_ids": "ghost", "conv_id": conv_id})
        assert r.status_code == 200
        assert "not found" in r.text.lower()

    @patch("RAG_Frontend.get_response_stream")
    def test_chat_streams_concatenated_chunks(self, mock_stream, client):
        mock_stream.side_effect = _fake_stream_side_effect(["Hello", " ", "world"])
        conv_id = self._make_conv_and_doc(client)
        r = client.post("/chat", data={"question": "Say hi", "doc_ids": "testdoc", "conv_id": conv_id})
        assert r.status_code == 200
        assert r.text == "Hello world"

    @patch("RAG_Frontend.get_response_stream")
    def test_chat_saves_user_and_assistant_messages(self, mock_stream, client):
        mock_stream.side_effect = _fake_stream_side_effect(["Answer"])
        conv_id = self._make_conv_and_doc(client)
        client.post("/chat", data={"question": "What is it?", "doc_ids": "testdoc", "conv_id": conv_id})
        msgs = client.get(f"/conversations/{conv_id}/messages").json()
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "What is it?"}
        assert msgs[1] == {"role": "assistant", "content": "Answer"}

    @patch("RAG_Frontend.get_response_stream")
    def test_chat_history_grows_across_turns(self, mock_stream, client):
        mock_stream.side_effect = _fake_stream_side_effect(["Reply"])
        conv_id = self._make_conv_and_doc(client)
        client.post("/chat", data={"question": "Q1", "doc_ids": "testdoc", "conv_id": conv_id})
        mock_stream.side_effect = _fake_stream_side_effect(["Reply2"])
        client.post("/chat", data={"question": "Q2", "doc_ids": "testdoc", "conv_id": conv_id})
        msgs = client.get(f"/conversations/{conv_id}/messages").json()
        assert len(msgs) == 4
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
