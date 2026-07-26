(() => {
  const docListEl = document.getElementById('doc-list');
  const newDocBtn = document.getElementById('new-doc');
  const emptyState = document.getElementById('empty-state');
  const editorEl = document.getElementById('editor');
  const titleInput = document.getElementById('title-input');
  const contentEl = document.getElementById('content');
  const saveStatus = document.getElementById('save-status');
  const toolbar = document.querySelector('.toolbar');
  const insertDiagramBtn = document.getElementById('insert-diagram');

  let currentId = null;
  let saveTimer = null;

  async function api(path, options) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    if (!res.ok && res.status !== 404) throw new Error(`request failed: ${res.status}`);
    return res.status === 204 || res.status === 404 ? null : res.json();
  }

  async function refreshList() {
    const docs = await api('/api/documents');
    docListEl.innerHTML = '';
    for (const doc of docs) {
      const li = document.createElement('li');
      li.dataset.id = doc.id;
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
      li.addEventListener('click', () => navigateTo(doc.id));
      docListEl.appendChild(li);
    }
    refreshActiveHighlight();
  }

  function closeEditor() {
    currentId = null;
    editorEl.hidden = true;
    emptyState.hidden = false;
    emptyState.textContent = 'Select a document, or create a new one to get started.';
    history.replaceState(null, '', location.pathname + location.search);
    refreshActiveHighlight();
  }

  // Every open document gets a shareable #doc/<id> link: pasting the URL
  // (or hitting back/forward) opens straight to that document via the
  // hashchange router below, instead of only being reachable by clicking
  // through the sidebar in this same session.
  function navigateTo(id) {
    if (location.hash === `#doc/${id}`) {
      openDoc(id);
    } else {
      location.hash = `doc/${id}`;
    }
  }

  async function openDoc(id) {
    const doc = await api(`/api/documents/${id}`);
    if (!doc) {
      currentId = null;
      editorEl.hidden = true;
      emptyState.hidden = false;
      emptyState.textContent = 'That document was not found — it may have been deleted.';
      refreshActiveHighlight();
      return;
    }
    currentId = id;
    emptyState.hidden = true;
    editorEl.hidden = false;
    titleInput.value = doc.title;
    contentEl.innerHTML = doc.content || '';
    window.CocoDiagram.hydrateAll(contentEl, scheduleSave);
    saveStatus.textContent = '';
    history.replaceState(null, '', `#doc/${id}`);
    refreshActiveHighlight();
  }

  function refreshActiveHighlight() {
    Array.from(docListEl.querySelectorAll('li')).forEach((li) => {
      li.classList.toggle('active', li.dataset.id === currentId);
    });
  }

  function routeFromHash() {
    const match = /^#doc\/(.+)$/.exec(location.hash);
    if (match) openDoc(match[1]);
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
    await refreshList();
    navigateTo(doc.id);
  });

  titleInput.addEventListener('input', scheduleSave);
  contentEl.addEventListener('input', scheduleSave);

  insertDiagramBtn.addEventListener('click', () => {
    contentEl.focus();
    const block = window.CocoDiagram.build(window.CocoDiagram.createDefaultData(), scheduleSave);
    const sel = window.getSelection();
    let range;
    if (sel && sel.rangeCount && contentEl.contains(sel.getRangeAt(0).commonAncestorContainer)) {
      range = sel.getRangeAt(0);
    } else {
      range = document.createRange();
      range.selectNodeContents(contentEl);
      range.collapse(false);
    }
    range.deleteContents();
    range.insertNode(block);
    range.setStartAfter(block);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
    scheduleSave();
  });

  toolbar.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-cmd]');
    if (!btn) return;
    e.preventDefault();
    contentEl.focus();
    document.execCommand(btn.dataset.cmd, false, btn.dataset.value || undefined);
    scheduleSave();
  });

  const copyLinkBtn = document.getElementById('copy-link');
  copyLinkBtn.addEventListener('click', async () => {
    if (!currentId) return;
    await navigator.clipboard.writeText(location.href);
    const original = saveStatus.textContent;
    saveStatus.textContent = 'Link copied';
    setTimeout(() => {
      if (saveStatus.textContent === 'Link copied') saveStatus.textContent = original;
    }, 1500);
  });

  window.addEventListener('hashchange', routeFromHash);

  refreshList().then(routeFromHash);
})();
