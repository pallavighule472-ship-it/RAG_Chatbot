import os
from dotenv import load_dotenv
from langsmith import Client, traceable

load_dotenv()

if os.getenv("LANGCHAIN_API_KEY"):
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
    os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "RAG_Project_QA")
    try:
        client = Client()
        client.read_project(project_name=os.environ["LANGCHAIN_PROJECT"])
        print(f"[LangSmith] Connected (Project: {os.environ['LANGCHAIN_PROJECT']})")
    except Exception as e:
        print(f"[LangSmith] Warning: {e}")
else:
    print("[LangSmith] Disabled (API Key not found)")

import asyncio
import hashlib
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import PromptTemplate
from langchain_core.tracers.langchain import LangChainTracer

INDEX_BASE_DIR = os.getenv("INDEX_BASE_DIR", "faiss_index")

model      = ChatOpenAI(model="gpt-4o-mini", streaming=True)
embeddings = OpenAIEmbeddings()

os.makedirs(INDEX_BASE_DIR, exist_ok=True)

Prompt = PromptTemplate(
    template="""You are a knowledgeable and precise assistant.
Use ONLY the provided context of document to answer the question.

Instructions:
- Answer clearly and concisely.
- Cite relevant parts of the context in your answer (if applicable).
- If multiple pieces of context are relevant, combine them logically.
- Do not include information outside the context.
- Maintain a professional tone.

Conversation History:
{chat_history}

Context:
{context}

Question:
{question}

Answer:""",
    input_variables=["chat_history", "context", "question"]
)


def make_doc_id(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()[:12]


def get_retriever(doc_id: str):
    index_path = os.path.join(INDEX_BASE_DIR, doc_id)
    if not os.path.exists(index_path):
        return None
    vs = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
    return vs.as_retriever(search_kwargs={"k": 5})


def index_document(file_path: str, doc_id: str):
    loader = PyPDFLoader(file_path) if file_path.endswith(".pdf") else TextLoader(file_path)
    documents = loader.load()
    splitter  = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=50)
    splits    = splitter.split_documents(documents)
    vs        = FAISS.from_documents(splits, embeddings)
    vs.save_local(os.path.join(INDEX_BASE_DIR, doc_id))


@traceable(name="RAG Chat Pipeline")
async def get_response_stream(
    question: str,
    history_list: list,
    doc_ids_str: str,
    filename_map: dict,
):
    doc_ids = [d.strip() for d in doc_ids_str.split(",") if d.strip()]
    if not doc_ids:
        return "No documents selected. Please select at least one document."

    async def fetch_doc_context(d_id):
        retriever = get_retriever(d_id)
        if retriever:
            docs         = await asyncio.to_thread(retriever.invoke, question)
            filename     = filename_map.get(d_id, "Unknown Document")
            doc_context  = "\n\n".join([d.page_content for d in docs])
            if doc_context:
                return f"--- From Document: {filename} ---\n{doc_context}"
        return None

    results       = await asyncio.gather(*(fetch_doc_context(d_id) for d_id in doc_ids))
    context_parts = [r for r in results if r]

    if not context_parts:
        return "Selected document(s) not found or have no relevant context."

    formatted_history = "\n".join([f"{m['role']}: {m['content']}" for m in history_list[-5:]])
    context           = "\n\n".join(context_parts)

    tracer = LangChainTracer(project_name=os.environ.get("LANGCHAIN_PROJECT"))
    chain  = Prompt | model
    return chain.astream(
        {"chat_history": formatted_history, "context": context, "question": question},
        config={"callbacks": [tracer]}
    )
