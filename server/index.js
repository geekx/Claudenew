const express = require('express');
const path = require('path');
const store = require('./store');

const app = express();
app.use(express.json({ limit: '2mb' }));
app.use(express.static(path.join(__dirname, '..', 'public')));

app.get('/api/documents', async (req, res) => {
  res.json(await store.listDocuments());
});

app.post('/api/documents', async (req, res) => {
  const doc = await store.createDocument({ title: req.body && req.body.title });
  res.status(201).json(doc);
});

app.get('/api/documents/:id', async (req, res) => {
  const doc = await store.getDocument(req.params.id);
  if (!doc) return res.status(404).json({ error: 'not found' });
  res.json(doc);
});

app.put('/api/documents/:id', async (req, res) => {
  const doc = await store.updateDocument(req.params.id, {
    title: req.body.title,
    content: req.body.content,
  });
  if (!doc) return res.status(404).json({ error: 'not found' });
  res.json(doc);
});

app.delete('/api/documents/:id', async (req, res) => {
  const ok = await store.deleteDocument(req.params.id);
  if (!ok) return res.status(404).json({ error: 'not found' });
  res.status(204).end();
});

const PORT = process.env.PORT || 3000;
if (require.main === module) {
  app.listen(PORT, () => console.log(`CoCo-doc listening on :${PORT}`));
}

module.exports = app;
