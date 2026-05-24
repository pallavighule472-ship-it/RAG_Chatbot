// ── Marked config ─────────────────────────────────────────────────────
marked.use({ breaks: true, gfm: true });

// ── Mobile sidebar toggle ─────────────────────────────────────────────
const _app = document.querySelector('.app');
document.getElementById('hamburgerBtn').addEventListener('click', () => _app.classList.toggle('sidebar-open'));
document.getElementById('sidebarOverlay').addEventListener('click', () => _app.classList.remove('sidebar-open'));

// ── State ─────────────────────────────────────────────────────────────
const state = {
  activeConvId:  null,
  selectedDocs:  new Set(),   // Set<doc_id>
  conversations: [],          // [{conv_id, title}]
  documents:     {},          // {doc_id: {doc_id, filename}}
  isStreaming:   false,
  messages:      [],          // [{role, content}] for active conv
};

// ── API helpers ───────────────────────────────────────────────────────
async function api(method, path, body = null) {
  const opts = { method };
  if (body !== null) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (r.status === 401) { location.href = '/login'; throw new Error('auth'); }
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status}`);
  return r.json();
}

// ── Data loaders ──────────────────────────────────────────────────────
async function fetchConversations() { state.conversations = await api('GET', '/conversations'); }
async function fetchDocuments()     { state.documents     = await api('GET', '/documents'); }
async function fetchMessages(id)    { state.messages      = await api('GET', `/conversations/${id}/messages`); }

// ── Conversation actions ──────────────────────────────────────────────
async function newConversation() {
  const c = await api('POST', '/conversations', { title: '' });
  state.conversations.push(c);
  state.activeConvId = c.conv_id;
  state.messages = [];
  renderConvs();
  renderMessages();
  return c;
}

async function switchConv(id) {
  if (id === state.activeConvId) return;
  state.activeConvId = id;
  await fetchMessages(id);
  renderConvs();
  renderMessages();
}

async function deleteConv(id) {
  await api('DELETE', `/conversations/${id}`);
  state.conversations = state.conversations.filter(c => c.conv_id !== id);
  if (state.activeConvId === id) {
    if (state.conversations.length) {
      await switchConv(state.conversations.at(-1).conv_id);
    } else {
      await newConversation();
    }
  }
  renderConvs();
}

async function clearAll() {
  if (!confirm('Delete all conversations? This cannot be undone.')) return;
  await api('DELETE', '/conversations');
  state.conversations = [];
  state.messages = [];
  await newConversation();
}

// ── Document actions ──────────────────────────────────────────────────
async function uploadFile(file) {
  showFeedback('loading', `Uploading ${file.name}…`);
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/upload', { method: 'POST', body: fd });
    if (r.status === 401) { location.href = '/login'; return; }
    const res = await r.json();
    if (res.status !== 'success') throw new Error(res.message);
    await fetchDocuments();
    state.selectedDocs.add(res.doc_id);
    renderDocs();
    updateDocsBar();
    updateInput();
    showFeedback('success', `✓ ${file.name}`);
    setTimeout(hideFeedback, 3000);
  } catch (e) {
    showFeedback('error', e.message || 'Upload failed');
  }
}

async function deleteDoc(id) {
  if (!confirm('Delete this document?')) return;
  await api('DELETE', `/documents/${id}`);
  delete state.documents[id];
  state.selectedDocs.delete(id);
  renderDocs();
  updateDocsBar();
  updateInput();
}

// ── Chat (streaming) ──────────────────────────────────────────────────
async function sendMessage(text) {
  if (!text.trim() || state.isStreaming || !state.selectedDocs.size) return;

  state.isStreaming = true;
  updateInput();

  // Push user message
  state.messages.push({ role: 'user', content: text });
  renderMessages();

  // Auto-title first message
  const conv = state.conversations.find(c => c.conv_id === state.activeConvId);
  if (conv && !conv.title) {
    const words = text.trim().split(/\s+/);
    const title = words.slice(0, 5).join(' ') + (words.length > 5 ? '…' : '');
    api('PATCH', `/conversations/${state.activeConvId}`, { title });
    conv.title = title;
    renderConvs();
  }

  // Push assistant placeholder with typing dots
  state.messages.push({ role: 'assistant', content: '' });
  renderMessages();
  const aEl = lastAssistantEl();
  if (aEl) aEl.querySelector('.msg-content').innerHTML = typingHTML();

  try {
    const fd = new FormData();
    fd.append('question', text);
    fd.append('doc_ids', [...state.selectedDocs].join(','));
    fd.append('conv_id', state.activeConvId);

    const res = await fetch('/chat', { method: 'POST', body: fd });
    if (res.status === 401) { location.href = '/login'; return; }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let full = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      full += dec.decode(value, { stream: true });
      state.messages.at(-1).content = full;
      if (aEl) {
        aEl.querySelector('.msg-content').innerHTML = marked.parse(full);
        aEl.classList.add('streaming');
        hljs.highlightAll();
        scrollBottom();
      }
    }

    if (aEl) aEl.classList.remove('streaming');
  } catch (e) {
    const err = `Error: ${e.message}`;
    state.messages.at(-1).content = err;
    if (aEl) { aEl.classList.remove('streaming'); aEl.querySelector('.msg-content').textContent = err; }
  } finally {
    state.isStreaming = false;
    updateInput();
    scrollBottom();
  }
}

// ── Rendering ─────────────────────────────────────────────────────────
function renderMessages() {
  const wrap = document.getElementById('messagesWrap');
  if (!state.messages.length) {
    wrap.innerHTML = emptyHTML();
    return;
  }
  wrap.innerHTML = state.messages.map(m => `
    <div class="message ${m.role}">
      <div class="msg-avatar">${m.role === 'user'
        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 12c2.76 0 5-2.24 5-5s-2.24-5-5-5-5 2.24-5 5 2.24 5 5 5zm0 2c-3.33 0-10 1.67-10 5v1h20v-1c0-3.33-6.67-5-10-5z"/></svg>'
        : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6z"/></svg>'
      }</div>
      <div class="msg-body">
        <div class="msg-label">${m.role === 'user'
          ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M12 12c2.76 0 5-2.24 5-5s-2.24-5-5-5-5 2.24-5 5 2.24 5 5 5zm0 2c-3.33 0-10 1.67-10 5v1h20v-1c0-3.33-6.67-5-10-5z"/></svg>'
          : '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6z"/></svg>'
        }</div>
        <div class="msg-content">${
          m.role === 'assistant' ? marked.parse(m.content || '') : esc(m.content)
        }</div>
      </div>
    </div>
  `).join('');
  hljs.highlightAll();
  scrollBottom();
}

function renderConvs() {
  const el = document.getElementById('convsList');
  if (!state.conversations.length) {
    el.innerHTML = '<div class="sb-empty">No conversations yet</div>';
    return;
  }
  el.innerHTML = [...state.conversations].reverse().map(c => `
    <div class="conv-item ${c.conv_id === state.activeConvId ? 'active' : ''}"
         data-id="${c.conv_id}" data-type="conv">
      <svg class="conv-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
      <span class="conv-title" title="${escA(c.title || 'New Chat')}">${esc(c.title || 'New Chat')}</span>
      <button class="btn-del" data-action="del-conv" data-id="${c.conv_id}" title="Delete">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>
  `).join('');
}

function renderDocs() {
  const el = document.getElementById('docsList');
  const docs = Object.values(state.documents);
  if (!docs.length) {
    el.innerHTML = '<div class="sb-empty">No documents uploaded</div>';
    return;
  }
  el.innerHTML = docs.map(d => `
    <div class="doc-item ${state.selectedDocs.has(d.doc_id) ? 'selected' : ''}">
      <input type="checkbox" data-action="toggle-doc" data-id="${d.doc_id}"
        ${state.selectedDocs.has(d.doc_id) ? 'checked' : ''}>
      <span class="doc-name" title="${escA(d.filename)}">${esc(d.filename)}</span>
      <button class="btn-del" data-action="del-doc" data-id="${d.doc_id}" title="Delete">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>
  `).join('');
}

function updateDocsBar() {
  const bar = document.getElementById('docsBar');
  const names = [...state.selectedDocs].map(id => state.documents[id]?.filename).filter(Boolean);
  bar.innerHTML = names.map(n => `
    <span class="doc-chip">
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
      </svg>
      ${esc(n)}
    </span>
  `).join('');
}

function updateInput() {
  const inp = document.getElementById('userInput');
  const btn = document.getElementById('sendBtn');
  const ready = state.selectedDocs.size > 0 && !state.isStreaming;
  inp.disabled = !ready;
  btn.disabled = !ready;
  inp.placeholder = !state.selectedDocs.size
    ? 'Select a document from the sidebar to start…'
    : state.isStreaming
    ? 'Generating response…'
    : 'Ask anything about your document(s)…';
}

// ── Helpers ───────────────────────────────────────────────────────────
const esc  = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const escA = s => esc(s).replace(/'/g,'&#39;');

function scrollBottom() {
  const w = document.getElementById('messagesWrap');
  w.scrollTop = w.scrollHeight;
}
function lastAssistantEl() {
  const els = document.querySelectorAll('.message.assistant');
  return els.length ? els[els.length - 1] : null;
}
function showFeedback(type, msg) {
  const el = document.getElementById('uploadFeedback');
  el.className = `upload-feedback ${type}`;
  el.textContent = msg;
  el.hidden = false;
}
function hideFeedback() { document.getElementById('uploadFeedback').hidden = true; }

function typingHTML() {
  return `<div class="typing"><span></span><span></span><span></span></div>`;
}
function emptyHTML() {
  return `
    <div class="empty-state">
      <div class="empty-icon">
        <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </div>
      <h2>Chat with your documents</h2>
      <p>Upload a PDF or text file, select it, and ask anything about its content.</p>
      <div class="empty-steps">
        <div class="empty-step"><div class="step-num">1</div>Upload a document in the sidebar</div>
        <div class="empty-step"><div class="step-num">2</div>Check it to select it</div>
        <div class="empty-step"><div class="step-num">3</div>Start asking questions</div>
      </div>
    </div>`;
}

// ── Events ────────────────────────────────────────────────────────────
document.addEventListener('click', async e => {
  const actionEl = e.target.closest('[data-action]');
  if (actionEl) {
    e.stopPropagation();
    const { action, id } = actionEl.dataset;
    if (action === 'del-conv') await deleteConv(id);
    if (action === 'del-doc')  await deleteDoc(id);
    return;
  }
  const ci = e.target.closest('.conv-item[data-type="conv"]');
  if (ci) await switchConv(ci.dataset.id);
});

document.addEventListener('change', e => {
  if (e.target.dataset.action === 'toggle-doc') {
    const id = e.target.dataset.id;
    e.target.checked ? state.selectedDocs.add(id) : state.selectedDocs.delete(id);
    renderDocs();
    updateDocsBar();
    updateInput();
  }
});

document.getElementById('newChatBtn').addEventListener('click', async () => {
  await newConversation();
  updateInput();
});

document.getElementById('clearAllBtn').addEventListener('click', clearAll);

// File upload
document.getElementById('fileInput').addEventListener('change', async e => {
  if (e.target.files[0]) { await uploadFile(e.target.files[0]); e.target.value = ''; }
});

// Drag & drop
const zone = document.getElementById('uploadZone');
zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('drag-over'); });
zone.addEventListener('dragleave', ()  => zone.classList.remove('drag-over'));
zone.addEventListener('drop', async e => {
  e.preventDefault();
  zone.classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (f) await uploadFile(f);
});

// Chat input
const inp = document.getElementById('userInput');
inp.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
});
inp.addEventListener('input', () => {
  inp.style.height = 'auto';
  inp.style.height = Math.min(inp.scrollHeight, 200) + 'px';
});
document.getElementById('sendBtn').addEventListener('click', submit);

function submit() {
  const text = inp.value.trim();
  if (!text || state.isStreaming) return;
  inp.value = ''; inp.style.height = 'auto';
  sendMessage(text);
}

// ── Init ──────────────────────────────────────────────────────────────
(async () => {
  try {
    await Promise.all([fetchConversations(), fetchDocuments()]);
    if (!state.conversations.length) {
      await newConversation();
    } else {
      state.activeConvId = state.conversations.at(-1).conv_id;
      await fetchMessages(state.activeConvId);
      renderConvs();
      renderMessages();
    }
    renderDocs();
    updateDocsBar();
    updateInput();
  } catch (e) {
    if (e.message === 'auth') return; // redirecting to /login
    document.getElementById('messagesWrap').innerHTML = `
      <div class="empty-state">
        <div class="empty-icon" style="background:#FEE2E2;color:#EF4444">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="8" x2="12" y2="12"/>
            <line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
        </div>
        <h2>Connection Error</h2>
        <p>Could not reach the backend. Make sure the server is running on port 8001.</p>
      </div>`;
  }
})();
