const fs = require('fs/promises');
const path = require('path');
const crypto = require('crypto');

const DATA_DIR = process.env.COCO_DATA_DIR || path.join(__dirname, '..', 'data');
const DATA_FILE = path.join(DATA_DIR, 'documents.json');

// Single in-memory map backed by one JSON file. Writes are serialized
// through `queue` so concurrent autosave requests can't interleave and
// corrupt the file (there is no real DB here, and json isn't safe for
// concurrent partial writes).
let queue = Promise.resolve();
let cache = null;

async function load() {
  if (cache) return cache;
  await fs.mkdir(DATA_DIR, { recursive: true });
  try {
    const raw = await fs.readFile(DATA_FILE, 'utf8');
    cache = JSON.parse(raw);
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
    cache = {};
  }
  return cache;
}

function persist() {
  queue = queue.then(() =>
    fs.writeFile(DATA_FILE, JSON.stringify(cache, null, 2), 'utf8')
  );
  return queue;
}

async function listDocuments() {
  const docs = await load();
  return Object.values(docs)
    .map(({ id, title, updatedAt }) => ({ id, title, updatedAt }))
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

async function getDocument(id) {
  const docs = await load();
  return docs[id] || null;
}

async function createDocument({ title } = {}) {
  const docs = await load();
  const id = crypto.randomUUID();
  const now = new Date().toISOString();
  const doc = { id, title: title || 'Untitled document', content: '', createdAt: now, updatedAt: now };
  docs[id] = doc;
  await persist();
  return doc;
}

async function updateDocument(id, { title, content }) {
  const docs = await load();
  const doc = docs[id];
  if (!doc) return null;
  if (title !== undefined) doc.title = title;
  if (content !== undefined) doc.content = content;
  doc.updatedAt = new Date().toISOString();
  await persist();
  return doc;
}

async function deleteDocument(id) {
  const docs = await load();
  if (!docs[id]) return false;
  delete docs[id];
  await persist();
  return true;
}

module.exports = { listDocuments, getDocument, createDocument, updateDocument, deleteDocument };
