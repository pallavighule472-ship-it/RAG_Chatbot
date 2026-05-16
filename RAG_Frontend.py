import os
import hmac
import hashlib
import shutil
import tempfile
import asyncio
import sqlite3
import uuid

from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, Depends, Cookie
from fastapi.responses import StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from RAG_Backend import index_document, get_response_stream, make_doc_id, INDEX_BASE_DIR

app     = FastAPI()
DB_FILE = os.getenv("DB_FILE", "chatbot.db")

# ── Auth ──────────────────────────────────────────────────────────────────────
# Set AUTH_PASSWORD in .env to enable. Leave unset for local dev (auth disabled).

AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")

def _session_token() -> str:
    return hmac.new(AUTH_PASSWORD.encode(), b"docchat", hashlib.sha256).hexdigest()

def _check_auth(docchat_session: str = Cookie(default="")) -> None:
    if AUTH_PASSWORD and not hmac.compare_digest(docchat_session, _session_token()):
        raise HTTPException(status_code=401, detail="Unauthorized")

# All protected API routes live on this router
router = APIRouter(dependencies=[Depends(_check_auth)])

# ── SQLite ────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id   TEXT PRIMARY KEY,
                filename TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                conv_id    TEXT PRIMARY KEY,
                title      TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS messages (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                conv_id TEXT NOT NULL,
                role    TEXT NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY (conv_id) REFERENCES conversations(conv_id) ON DELETE CASCADE
            );
        """)

init_db()

# ── Pydantic models ───────────────────────────────────────────────────────────

class ConversationIn(BaseModel):
    title: str = ""

class TitleUpdate(BaseModel):
    title: str

# ── Document endpoints ────────────────────────────────────────────────────────

@router.get("/documents")
async def list_documents():
    with get_db() as conn:
        rows = conn.execute("SELECT doc_id, filename FROM documents").fetchall()
    return {r["doc_id"]: {"doc_id": r["doc_id"], "filename": r["filename"]} for r in rows}

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    suffix  = os.path.splitext(file.filename)[1]
    content = await file.read()
    doc_id  = make_doc_id(content)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        await asyncio.to_thread(index_document, tmp_path, doc_id)
        os.remove(tmp_path)
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO documents (doc_id, filename) VALUES (?, ?)",
                (doc_id, file.filename)
            )
        return {"status": "success", "message": f"Indexed {file.filename}", "doc_id": doc_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    with get_db() as conn:
        result = conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Document not found")
    index_path = os.path.join(INDEX_BASE_DIR, doc_id)
    if os.path.exists(index_path):
        shutil.rmtree(index_path)
    return {"status": "success"}

# ── Chat endpoint ─────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(question: str = Form(...), doc_ids: str = Form(...), conv_id: str = Form(...)):
    with get_db() as conn:
        history_rows = conn.execute(
            "SELECT role, content FROM messages WHERE conv_id = ? ORDER BY id ASC", (conv_id,)
        ).fetchall()
        doc_rows = conn.execute("SELECT doc_id, filename FROM documents").fetchall()

    history_list = [{"role": r["role"], "content": r["content"]} for r in history_rows][-10:]
    filename_map = {r["doc_id"]: r["filename"] for r in doc_rows}

    with get_db() as conn:
        conn.execute(
            "INSERT INTO messages (conv_id, role, content) VALUES (?, ?, ?)",
            (conv_id, "user", question)
        )

    async def stream_generator():
        full_parts = []
        try:
            gen = await get_response_stream(question, history_list, doc_ids, filename_map)
            if isinstance(gen, str):
                full_parts.append(gen)
                yield gen
                return
            async for chunk in gen:
                if chunk.content:
                    full_parts.append(chunk.content)
                    yield chunk.content
        except Exception as e:
            err = f"Error: {str(e)}"
            full_parts.append(err)
            yield err
        finally:
            if full_parts:
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO messages (conv_id, role, content) VALUES (?, ?, ?)",
                        (conv_id, "assistant", "".join(full_parts))
                    )

    return StreamingResponse(stream_generator(), media_type="text/plain")

# ── Conversation endpoints ────────────────────────────────────────────────────

@router.get("/conversations")
async def list_conversations():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT conv_id, title FROM conversations ORDER BY created_at ASC"
        ).fetchall()
    return [{"conv_id": r["conv_id"], "title": r["title"]} for r in rows]

@router.post("/conversations")
async def create_conversation(body: ConversationIn):
    conv_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute(
            "INSERT INTO conversations (conv_id, title) VALUES (?, ?)",
            (conv_id, body.title)
        )
    return {"conv_id": conv_id, "title": body.title}

@router.patch("/conversations/{conv_id}")
async def update_conversation_title(conv_id: str, body: TitleUpdate):
    with get_db() as conn:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE conv_id = ?",
            (body.title, conv_id)
        )
    return {"status": "success"}

@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM conversations WHERE conv_id = ?", (conv_id,))
    return {"status": "success"}

@router.delete("/conversations")
async def clear_all_conversations():
    with get_db() as conn:
        conn.executescript("DELETE FROM messages; DELETE FROM conversations;")
    return {"status": "success"}

@router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: str):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conv_id = ? ORDER BY id ASC",
            (conv_id,)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]

# ── Auth endpoints (public) ───────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

@app.get("/login", include_in_schema=False)
async def login_page():
    return FileResponse("static/login.html")

@app.post("/login", include_in_schema=False)
async def do_login(password: str = Form(...)):
    if not AUTH_PASSWORD or password == AUTH_PASSWORD:
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie("docchat_session", _session_token(), httponly=True, samesite="lax")
        return resp
    return RedirectResponse("/login?error=1", status_code=302)

@app.post("/logout", include_in_schema=False)
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("docchat_session")
    return resp

# ── Static frontend (HTML/JS/CSS) ─────────────────────────────────────────────

app.include_router(router)
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_frontend(docchat_session: str = Cookie(default="")):
    if AUTH_PASSWORD and not hmac.compare_digest(docchat_session, _session_token()):
        return RedirectResponse("/login")
    return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    print("Starting RAG Chatbot at http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001)
