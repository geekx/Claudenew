(() => {
  const docListEl = document.getElementById('doc-list');
  const newDocBtn = document.getElementById('new-doc');
  const emptyState = document.getElementById('empty-state');
  const editorEl = document.getElementById('editor');
  const titleInput = document.getElementById('title-input');
  const contentEl = document.getElementById('content');
  const saveStatus = document.getElementById('save-status');
  const toolbar = document.querySelector('.toolbar');

  let currentId = null;
  let saveTimer = null;

  async function api(path, options) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    if (!res.ok && res.status !== 404) throw new Error(`request failed: ${res.status}`);
    return res.status === 204 ? null : res.json();
  }

  async function refreshList(selectId) {
    const docs = await api('/api/documents');
    docListEl.innerHTML = '';
    for (const doc of docs) {
      const li = document.createElement('li');
      li.className = doc.id === currentId ? 'active' : '';
      const titleSpan = document.createElement('span');
      titleSpan.className = 'doc-title';
      titleSpan.textContent = doc.title || 'Untitled document';
      const delBtn = document.createElement('button');
      delBtn.className = 'delete-btn';
      delBtn.textContent = '×';
      delBtn.title = 'Delete';
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete "${doc.title}"?`)) return;
        await api(`/api/documents/${doc.id}`, { method: 'DELETE' });
        if (currentId === doc.id) closeEditor();
        refreshList();
      });
      li.append(titleSpan, delBtn);
      li.addEventListener('click', () => openDoc(doc.id));
      docListEl.appendChild(li);
    }
    if (selectId) openDoc(selectId);
  }

  function closeEditor() {
    currentId = null;
    editorEl.hidden = true;
    emptyState.hidden = false;
  }

  async function openDoc(id) {
    const doc = await api(`/api/documents/${id}`);
    if (!doc) return;
    currentId = id;
    emptyState.hidden = true;
    editorEl.hidden = false;
    titleInput.value = doc.title;
    contentEl.innerHTML = doc.content || '';
    saveStatus.textContent = '';
    Array.from(docListEl.children).forEach((li, i) => {});
    refreshActiveHighlight();
  }

  function refreshActiveHighlight() {
    Array.from(docListEl.querySelectorAll('li')).forEach((li) => {
      li.classList.remove('active');
    });
  }

  function scheduleSave() {
    saveStatus.textContent = 'Saving…';
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 500);
  }

  async function save() {
    if (!currentId) return;
    await api(`/api/documents/${currentId}`, {
      method: 'PUT',
      body: JSON.stringify({ title: titleInput.value, content: contentEl.innerHTML }),
    });
    saveStatus.textContent = 'Saved';
    refreshList();
  }

  newDocBtn.addEventListener('click', async () => {
    const doc = await api('/api/documents', { method: 'POST', body: JSON.stringify({}) });
    await refreshList(doc.id);
  });

  titleInput.addEventListener('input', scheduleSave);
  contentEl.addEventListener('input', scheduleSave);

  toolbar.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-cmd]');
    if (!btn) return;
    e.preventDefault();
    contentEl.focus();
    document.execCommand(btn.dataset.cmd, false, btn.dataset.value || undefined);
    scheduleSave();
  });

  refreshList();
})();
