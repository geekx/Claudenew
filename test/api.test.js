const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs/promises');
const os = require('os');
const path = require('path');

let app;
let tmpDir;

test.before(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'coco-doc-test-'));
  process.env.COCO_DATA_DIR = tmpDir;
  app = require('../server/index');
});

test.after(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

async function request(method, url, body) {
  const server = app.listen(0);
  const { port } = server.address();
  try {
    const res = await fetch(`http://127.0.0.1:${port}${url}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const status = res.status;
    const json = status === 204 ? null : await res.json();
    return { status, json };
  } finally {
    server.close();
  }
}

test('document CRUD lifecycle', async () => {
  const created = await request('POST', '/api/documents', { title: 'My doc' });
  assert.equal(created.status, 201);
  assert.equal(created.json.title, 'My doc');
  const id = created.json.id;

  const list = await request('GET', '/api/documents');
  assert.equal(list.status, 200);
  assert.ok(list.json.some((d) => d.id === id));

  const updated = await request('PUT', `/api/documents/${id}`, { content: '<p>hi</p>' });
  assert.equal(updated.status, 200);
  assert.equal(updated.json.content, '<p>hi</p>');

  const fetched = await request('GET', `/api/documents/${id}`);
  assert.equal(fetched.json.content, '<p>hi</p>');

  const deleted = await request('DELETE', `/api/documents/${id}`);
  assert.equal(deleted.status, 204);

  const missing = await request('GET', `/api/documents/${id}`);
  assert.equal(missing.status, 404);
});
