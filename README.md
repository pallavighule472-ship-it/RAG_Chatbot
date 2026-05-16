# DocChat — RAG Chatbot

> Upload PDF or text documents and chat with them using AI. Answers are grounded strictly in your document's content — no hallucination outside the provided context.

<!-- Replace the line below with an actual demo GIF (record with ScreenToGif on Windows) -->
<!-- ![Demo](docs/demo.gif) -->

---

## Features

- **Multi-document support** — upload multiple PDFs/TXTs and query across all simultaneously
- **Streaming responses** — answers appear word-by-word in real time
- **Persistent conversation history** — resume any past conversation across sessions
- **Parallel retrieval** — FAISS indexes queried concurrently across selected documents
- **Clean architecture** — AI engine fully decoupled from the web/data layer
- **LangSmith tracing** — every LLM call is observable end-to-end
- **21 passing tests** — full API test suite with isolated databases

---

## Architecture

```
┌─────────────────────────────────┐
│   Browser  (HTML / CSS / JS)    │
│   static/index.html             │
│   static/style.css              │
│   static/app.js                 │
└────────────┬────────────────────┘
             │  REST API  (fetch / streaming)
             ▼
┌─────────────────────────────────┐
│   RAG_Frontend.py  (FastAPI)    │
│                                 │
│   • All API routes              │
│   • SQLite  ←──→  chatbot.db   │
│     - documents                 │
│     - conversations             │
│     - messages                  │
│   • Serves static UI            │
└────────────┬────────────────────┘
             │  Python imports
             ▼
┌─────────────────────────────────┐
│   RAG_Backend.py  (AI Engine)   │
│                                 │
│   • FAISS vector search         │
│   • LangChain RAG chain         │
│   • OpenAI GPT-4o-mini          │
│   • OpenAI Embeddings           │
│   • LangSmith tracing           │
└─────────────────────────────────┘
```

---

## Tech Stack

| Layer         | Technology                          |
|---------------|-------------------------------------|
| LLM           | OpenAI GPT-4o-mini                  |
| Embeddings    | OpenAI text-embedding-ada-002       |
| Vector Store  | FAISS (per-document local indexes)  |
| Orchestration | LangChain                           |
| Backend       | FastAPI + Python                    |
| Database      | SQLite (WAL mode, FK constraints)   |
| Frontend      | Vanilla HTML / CSS / JavaScript     |
| Observability | LangSmith                           |
| Tests         | pytest + FastAPI TestClient         |

---

## Project Structure

```
RAG_Chatbot/
├── RAG_Backend.py       # AI engine — FAISS, LLM, embeddings, LangSmith
├── RAG_Frontend.py      # FastAPI app — SQLite, API routes, static serving
├── Run.py               # Entry point — starts server and opens browser
├── Requirements.txt
├── .env                 # API keys (not committed)
│
├── static/
│   ├── index.html       # App shell
│   ├── style.css        # All styles
│   └── app.js           # Vanilla JS — state, streaming, rendering
│
├── tests/
│   └── test_api.py      # 21 API tests
│
├── faiss_index/         # Per-document FAISS indexes  (gitignored)
└── chatbot.db           # SQLite database             (gitignored)
```

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-username/RAG_Chatbot.git
cd RAG_Chatbot
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Mac / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r Requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=your_openai_api_key

# Optional — enables LangSmith tracing
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_PROJECT=RAG_Project_QA
```

### 5. Run the app

```bash
python Run.py
```

The browser opens automatically at **http://localhost:8001**

---

## How to Use

1. **Upload a document** — drag and drop or click *Upload PDF or TXT* in the sidebar
2. **Select it** — check the checkbox next to the document name
3. **Ask a question** — type in the input box and press **Enter**
4. **Multi-document** — select multiple documents to query across all of them at once
5. **Resume a conversation** — click any past conversation in the sidebar

---

## Running with Docker

### Build and start

```bash
docker-compose up --build
```

Open **http://localhost:8001** in your browser.

### Stop

```bash
docker-compose down
```

Data (SQLite database + FAISS indexes) is stored in a named Docker volume (`app_storage`) and survives container restarts. To wipe all data:

```bash
docker-compose down -v
```

---

## Running Tests

```bash
pytest tests/test_api.py -v
```

All 21 tests run against isolated temporary databases — no OpenAI API calls are made.

```
tests/test_api.py::TestMakeDocId               (3 tests)
tests/test_api.py::TestDocuments               (5 tests)
tests/test_api.py::TestConversations           (9 tests)
tests/test_api.py::TestChat                    (4 tests)
```

---

## How It Works

### Upload & Indexing
1. File is saved to a temp path
2. Split into chunks (600 tokens, 50 overlap) using `RecursiveCharacterTextSplitter`
3. Each chunk is embedded via OpenAI Embeddings
4. Embeddings saved to a per-document FAISS index under `faiss_index/{doc_id}/`
5. Document registered in SQLite

### Query & Answer
1. Top-5 relevant chunks retrieved in parallel across all selected documents
2. Retrieved context + last 5 conversation turns passed to GPT-4o-mini via LangChain
3. Response streams back chunk-by-chunk to the browser
4. Full response saved to SQLite once streaming completes

---

## Adding a Demo GIF

Record a short screen capture (upload a doc → ask a question → see streaming response) and save it as `docs/demo.gif`. Then uncomment the demo line at the top of this file.

**Recommended tools:**
- **Windows** — [ScreenToGif](https://www.screentogif.com/) (free)
- **Mac** — [Kap](https://getkap.co/) (free)

---

## License

MIT
