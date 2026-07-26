# CoCo-doc Roadmap

CoCo-doc is a lightweight, self-hostable collaborative document editor —
a simpler, faster alternative to heavyweight tools like Google Docs or Notion,
aimed at small teams and individuals who want quick rich-text collaboration
without the operational overhead.

## Vision

Let anyone spin up a fast, minimal-friction place to write and co-edit
documents — no accounts-heavy onboarding, no bloat, real-time when it
matters.

## Target personas

- **Solo writer** — drafts notes/docs, wants a clean rich-text editor with
  zero setup friction.
- **Small team collaborator** — shares a doc link with teammates, expects
  concurrent edits to just work.
- **Self-hoster/admin** — runs CoCo-doc on modest infrastructure, cares
  about low resource footprint and simple deploys.

## Prioritization framework

Phases are scoped Now / Next / Later (per standard PM roadmap practice),
each phase shippable and independently valuable, so the roadmap stays
resilient to reprioritization.

### Now — v0.1 (MVP: single-user document editing)
- [x] Project scaffold (Express API + static rich-text frontend)
- [x] Document CRUD (create, list, open, edit, delete)
- [x] Rich text editing: bold/italic/underline, headings, lists
- [x] Autosave (debounced) with persistent storage
- **Success metric:** time from landing on the app to a saved first
  document < 30s.

### Next — v0.2 (Sharing & history)
- [ ] Shareable document links (unguessable IDs already in place from v0.1)
- [ ] Version history / restore previous autosave snapshots
- [ ] Basic lightweight auth (per-user document ownership)
- **Success metric:** a shared link opens to the latest content with no
  manual refresh needed.

### Next — v0.3 (Real-time collaboration)
- [ ] WebSocket transport
- [ ] CRDT-based concurrent editing (e.g. Yjs) to avoid last-write-wins
  data loss
- [ ] Presence indicators (remote cursors, active-user avatars)
- **Success metric:** two clients editing the same doc converge with no
  lost keystrokes under normal network conditions.

### Later — v0.4 (Review workflows)
- [ ] Comments and suggestion/track-changes mode
- [ ] Export to Markdown / PDF
- [ ] Full-text search across a user's documents

### Later — v0.5 (Multi-tenant polish)
- [ ] Roles/permissions (owner/editor/viewer)
- [ ] Workspaces/organizations
- [ ] Mobile-responsive editor pass

## Process notes

This roadmap is maintained by an autonomous hourly build loop. Each pass:
1. Re-reads this file to find the next unchecked item in the earliest
   incomplete phase.
2. Implements it (or a meaningful slice of it), commits, and pushes to the
   working branch.
3. Checks items off here as they land, and revisits phase ordering when
   new information (usage, feedback, technical constraints) warrants it —
   in the spirit of iterative roadmap review rather than a fixed plan.
