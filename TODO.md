# TODO.md — Build Sequence

Ordered work packages for Tinker. Complete each package (tests passing, ruff clean, mypy strict clean) before starting the next, unless the parallelism notes indicate otherwise.

Refer to `requirements.md` for full specifications. Section references (e.g., §3.1) point there.

---

## WP-01: Project Scaffolding ✅

Set up the project skeleton and foundational infrastructure.

- [x] Directory structure per CLAUDE.md
- [x] `pyproject.toml` with all dependencies and dev dependency group
- [x] Quart app factory (`app/__init__.py`)
- [x] Config module (`app/core/config.py`): load environment variables (§8.1), `.env` support
- [x] AsyncEngine + `async_sessionmaker` setup (`app/core/database.py`)
- [x] SQLite PRAGMAs (WAL mode, busy_timeout) via engine connect event
- [x] Request-scoped session lifecycle (`before_request` / `teardown_appcontext`)
- [x] Alembic initialisation with sync engine in `env.py` (§7.5)
- [x] ruff config, mypy strict config, pytest + pytest-asyncio config
- [x] `.gitignore` (include `.env`, `db/tinker.db`, `media/`, `__pycache__/`)
- [x] Verify: `uv sync`, `ruff check`, `mypy`, `pytest` all pass on empty project

**Produces:** Runnable Quart app that starts, connects to SQLite, and shuts down cleanly. No routes yet.

---

## WP-02: Models + Initial Migration ✅

SQLAlchemy ORM models and the first Alembic migration.

- [x] Base model class with UUID primary key convention
- [x] `Note` model (§3.1)
- [x] `RemoteActor` model (actor cache with TTL: URI, display name, handle, avatar URL, inbox URL, shared inbox URL, public key, fetched_at)
- [x] `Follower` model (§4.6: actor URI, inbox URL, shared inbox URL, display name, avatar URL, status)
- [x] `Following` model (§4.6: actor URI, inbox URL, display name, avatar URL, status)
- [x] `TimelineItem` model (received notes/boosts for the home timeline: activity type, actor, content, original object URI, received_at)
- [x] `Notification` model (§5.5: type, actor URI, actor name, object URI, read status, created_at)
- [x] `DeliveryQueue` model (§6.2: activity JSON, target inbox, status, attempts, next_retry_at, created_at)
- [x] `Settings` model (§8.2: key-value pairs)
- [x] `MediaAttachment` model (note FK, file path, MIME type, alt text, uploaded_at)
- [x] `Like` model (tracking outgoing likes: note URI, actor URI, activity URI)
- [x] `Keypair` model (RSA public/private key storage, created_at)
- [x] Repository classes for each model
- [x] Initial Alembic migration (`001_initial.py`)
- [x] Unit tests for all repository classes (mocked, no DB)

**Produces:** Complete schema, migrated database, tested data access layer.

**Depends on:** WP-01

---

## WP-03: Configuration + Settings Service ✅

Wire up both configuration layers so they're usable by everything that follows.

- [x] Settings service: get/set with typed accessors for known keys (§8.2)
- [x] Settings repository
- [x] Seed default settings on first run (empty display_name, bio, links)
- [x] Tests for settings service and repository

**Produces:** Working config (env vars) and settings (DB) available to the app.

**Depends on:** WP-02

---

## WP-04: Actor, WebFinger, NodeInfo ✅

The identity layer — everything remote servers need to discover and address this instance. The `/{username}` route is dual-purpose: it serves the public profile page for browsers and the JSON-LD actor document for AP consumers.

- [x] RSA keypair generation on first run, stored via Keypair model
- [x] Actor document endpoint at `GET /{username}` (§4.1): JSON-LD response with public key when `Accept` is `application/activity+json` or `application/ld+json`
- [x] Public profile page at `GET /{username}`: self-contained static HTML (inline CSS/JS) with profile content (display name, bio, avatar, handle, links) injected server-side via simple string interpolation from the settings table when `Accept` is `text/html` or browser default (§2.3)
- [x] Content negotiation logic on `/{username}` to dispatch between HTML and JSON-LD responses
- [x] WebFinger endpoint at `GET /.well-known/webfinger` (§4.1): returns `self` link pointing to `/{username}`
- [x] NodeInfo endpoints at `GET /.well-known/nodeinfo` and the referenced NodeInfo document (§4.1)
- [x] Integration tests: WebFinger returns correct self link, actor document is valid JSON-LD, NodeInfo reports correct stats, browser request to `/{username}` returns HTML profile with expected content, AP request to `/{username}` returns JSON-LD
- [x] "Follow me" link on the public profile page (§2.3): a plain `<a>` whose `href` is the actor's full AP URI, allowing Fediverse clients to resolve it into a follow action

**Produces:** Discoverable ActivityPub actor with a public profile page. Remote servers can find and address this instance. Visitors see the profile in a browser.

**Depends on:** WP-03

---

## WP-05: HTTP Signatures ✅

Signing and verification — required before any inbox/outbox work.

- [x] Signature signing module (`app/federation/signatures.py`): sign outgoing requests using the stored RSA keypair (draft-cavage-http-signatures, RSA-SHA256)
- [x] Signature verification module: verify incoming requests against the sender's cached public key
- [x] Re-fetch fallback (§4.2): on verification failure, fetch actor document fresh and retry once
- [x] Remote actor fetching service: retrieve and cache actor documents with TTL (§4.7)
- [x] Key rotation: mechanism to regenerate the local keypair and update the actor document
- [x] Unit tests for sign/verify round trip
- [x] Integration test: verify re-fetch on signature failure

**Produces:** All outgoing HTTP requests can be signed; all incoming inbox requests can be verified.

**Depends on:** WP-04

---

## WP-06: Authentication + Sessions ✅

Admin auth — required before any admin routes.

- [x] User table or config-based admin credential (single user — password hash stored in settings or a dedicated table)
- [x] Login route at `GET /login` serving self-contained static HTML page from `static/pages/login.html` (inline CSS/JS, simple form with username + password, POSTs to auth endpoint)
- [x] Login POST endpoint: validate credentials, create session
- [x] argon2 password hashing
- [x] Server-side session with secure cookie (HttpOnly, Secure, SameSite=Strict) (§5.1)
- [x] CSRF token middleware on all state-changing endpoints
- [x] Rate limiting on login endpoint
- [x] Auth guard: decorator or middleware that protects `/admin/*` routes
- [x] Tests for login flow, session creation, CSRF validation, rate limiting

**Produces:** Working auth. Admin routes can be protected. Login page served at `/login`.

**Depends on:** WP-03

---

## WP-07: Home Page ✅

The static home page at `/`.

- [x] Self-contained static HTML file at `static/pages/home.html` with all CSS and JS inline (§2.2)
- [x] Route at `GET /` serving the file — no dynamic data, no API calls, no authentication
- [x] Test: home page returns 200 with expected HTML content

**Produces:** Public home page at `/`.

**Depends on:** WP-01

---

## WP-08: Note Publishing + Outbox ✅

Create notes and generate the corresponding AP activities. Delivery comes in WP-09.

- [x] Schema migration: add `bio`, `header_image_url` to `remote_actors`; add `content` to `notifications` (§4.7, §5.5)
- [x] Note service: create, edit, delete operations (§3.1, §3.2)
- [x] Markdown → HTML rendering
- [x] AP object endpoint at `GET /notes/{id}` (§2.4): JSON-LD for AP consumers, `302 → /` for browsers
- [x] `Create{Note}` activity generation on publish (§4.3)
- [x] `Update{Note}` activity generation on edit
- [x] `Delete` activity generation (with Tombstone) on delete
- [x] Outbox collection endpoint at `GET /{username}/outbox` (§4.3): paginated OrderedCollection
- [x] Tests for note CRUD, AP object serialisation, content negotiation, outbox pagination

**Produces:** Notes can be created, edited, deleted. AP objects are fetchable. Activities are generated (but not yet delivered).

**Depends on:** WP-05

---

## WP-09: Delivery Pipeline ✅

Fan-out activities to follower inboxes.

- [x] Delivery service (`app/federation/delivery.py`): persist to `DeliveryQueue`, fan-out with shared inbox dedup (§6.3)
- [x] Semaphore-bounded async delivery tasks (§6.1)
- [x] HTTP Signature signing on each delivery request
- [x] Status tracking: pending → delivered / failed
- [x] Exponential backoff on failure (1m → 5m → 30m → 2h → 12h), max 5 retries (§6.3)
- [x] Dead instance detection: flag after 7 days of consecutive failures (§6.3)
- [x] Crash recovery on startup: re-enqueue incomplete deliveries (§6.2)
- [x] Wire note publishing (WP-08) to trigger delivery
- [x] Integration test with mock AP server: publish note → verify `Create{Note}` arrives with valid signature

**Produces:** Published notes are delivered to all followers. Failed deliveries retry with backoff.

**Depends on:** WP-08

---

## WP-10: Inbox Processing ✅

Receive and process incoming activities from remote servers.

- [x] Inbox endpoint at `POST /{username}/inbox` (§4.4): verify signature, return `202 Accepted`, dispatch async processing
- [x] Activity handlers for all supported types (§4.4 table):
  - `Follow` → auto-accept, send `Accept{Follow}`, store follower
  - `Undo{Follow}` → remove follower
  - `Create{Note}` → store in timeline (if from followed actor)
  - `Announce` → store in timeline, resolve original object
  - `Like` → store notification
  - `Delete` → remove from timeline/cache
  - `Update` → overwrite cached object
  - `Undo{Like}` / `Undo{Announce}` → remove record
  - `Accept{Follow}` → mark follow as accepted
  - `Reject{Follow}` → mark follow as rejected, remove
- [x] Notification creation for relevant activity types (likes, boosts, follows, replies)
- [x] Integration tests with mock AP server: mock sends Follow → verify Accept returned; mock sends Create{Note} → verify timeline insertion; mock sends Like → verify notification

**Produces:** Fully functional inbox. The instance can receive and process all specified activity types.

**Depends on:** WP-05, WP-09 (needs delivery for sending Accept{Follow})

---

## WP-11: Followers + Following Management ✅

Outgoing follow requests and collection endpoints.

- [x] Follow service: send `Follow` activity to remote actor's inbox (§4.5)
- [x] Unfollow: send `Undo{Follow}` (§4.5)
- [x] Followers collection endpoint at `GET /{username}/followers` (§4.6): paginated OrderedCollection
- [x] Following collection endpoint at `GET /{username}/following` (§4.6): paginated OrderedCollection
- [x] Integration test: follow mock actor → verify Follow arrives → mock sends Accept → verify following status updated

**Produces:** Can follow/unfollow remote actors. Collection endpoints work for federation.

**Depends on:** WP-10

---

## WP-12: Media Upload + Processing ✅

Image handling for note attachments and avatar uploads.

- [x] Upload endpoint (admin-protected): accept image file via multipart form
- [x] Validation: MIME type allowlist (JPEG, PNG, WebP, GIF, HEIC), max file size (§3.3)
- [x] Pillow processing: strip metadata (EXIF, IPTC, XMP), optimise (§3.3)
- [x] `pillow-heif`: detect HEIC and convert to JPEG
- [x] Store optimised file to configured media path
- [x] Create MediaAttachment record
- [x] Serve uploaded media via static file route or Caddy passthrough
- [x] Avatar proxying: fetch remote avatar URLs to local storage at `/media/avatars/` (§9)
- [x] Tests for upload validation, metadata stripping, HEIC conversion, avatar proxying


**Produces:** Images can be uploaded, processed, and served. Remote avatars are proxied.

**Depends on:** WP-06

---

## WP-13: Admin Timeline ✅

The primary admin view — static HTML shell with Web Components fetching from a JSON API.

- [x] **Base admin JSON API patterns:** auth-protected JSON endpoints with consistent `{ "data": [...], "cursor": ..., "has_more": bool }` envelope; `GET /admin/api/csrf` for CSRF token
- [x] **Foundational leaf components:**
  - `<actor-identity>`: avatar + display name + handle grouping; `size` attribute (`sm` / `md` / `lg`)
  - `<nav-bar>`: admin navigation (Home, Profile, Notifications, Likes, Following, Followers, search icon); `active` attribute; dispatches `show-search-modal` event; `<notification-badge>` slot renders empty until WP-16
- [x] Timeline JSON API endpoint (`GET /admin/api/timeline`): merged own notes + `TimelineItem` records, with liked state per item; `since` / `before` cursor params; 20-item pages
- [x] `<timeline-view>` Web Component: fetches timeline, 30 s poll for new items, deduplication, "Load more" cursor pagination
- [x] `<status-item>` Web Component: avatar, relative timestamp, rendered HTML content, optional media, like/repost/reply/edit/delete action buttons; `own` attribute enables edit and delete controls; inline edit and reply forms
- [x] `<compose-box>` Web Component: textarea, Ctrl/Cmd+Enter shortcut, POST to notes JSON API, fires `post-submitted`; image attachment UI (file picker, preview strip) wired to media upload API
- [x] Static HTML shell at `static/admin/timeline.html` injects `window.__TINKER__.csrf`
- [x] Admin CSS: `.admin-page`, `.admin-nav`, `.compose-box`, `.status-item`, `.actor-identity`, `.timeline-view__*` with logical properties and CSS custom properties throughout
- [x] Tests for timeline JSON API: auth required, CSRF endpoint, response shape, item fields, own notes in timeline (`own: true`, `internal_id`), `since` / `before` pagination, invalid timestamp → 400

**Produces:** Working admin home view with real-time-ish timeline. JSON API endpoints and Web Component patterns established for reuse by other admin views.

**Depends on:** WP-06, WP-10 (timeline items come from inbox), WP-08 (own notes)

---

## WP-14: Admin Compose + Media Attachment ✅

Full compose flow including image attachments. The `<compose-box>` component is defined in WP-13; this WP wires in media support.

- [x] Wire `<compose-box>` component to note creation (WP-08) + delivery (WP-09) via `POST /admin/api/notes`
- [x] Image attachment: upload via `fetch()` + `FormData` to `POST /admin/api/media`, show client-side object URL preview immediately, replace with server URL on success
- [x] Attach MediaAttachment records to notes via `attachment_ids` field in create-note request
- [x] Include attachments in outgoing `Create{Note}` activity as AP `attachment` objects (Document type with `mediaType` and `url`)
- [x] Tests: upload → compose with `attachment_ids` → 201, timeline item shows `media_url`, invalid attachment IDs silently skipped, upload without note association returns `id` + `url`

**Produces:** Can compose and publish notes with image attachments from the admin.

**Depends on:** WP-13, WP-12

---

## WP-15: Admin Interactions

Like, reply, boost, edit, delete — all from the timeline via Web Components and JSON API.

- [ ] Like/unlike toggle: `fetch()` call to JSON API → generate and deliver `Like` / `Undo{Like}` activity (§4.5), update `<status-item>` button state
- [ ] Boost/unboost toggle: `fetch()` call to JSON API → generate and deliver `Announce` / `Undo{Announce}` activity (§4.5), update `<status-item>` button state
- [ ] Reply: inline compose form within `<status-item>` → publish as `Create{Note}` with `inReplyTo` → deliver to author + followers (§4.5)
- [ ] Edit own note: open edit form in `<status-item>` → update note via JSON API → deliver `Update{Note}` (§4.3)
- [ ] Delete own note: confirm → delete note via JSON API → deliver `Delete` with Tombstone (§4.3)
- [ ] Tests for each interaction type: JSON API call, activity generation, delivery

**Produces:** Full interaction capability from the admin timeline.

**Depends on:** WP-13, WP-09

---

## WP-16: SSE Notifications

Real-time notification push to the admin.

- [ ] `asyncio.Queue` bridge: inbox pipeline (WP-10) emits notification events to the queue
- [ ] SSE endpoint (admin-protected): reads from queue, streams events to client (§5.3)
- [ ] Unread count JSON API endpoint (`GET /admin/api/notifications/unread-count`): returns count of `read = false` rows
- [ ] `<notification-badge>` Web Component: fetches unread count on init, increments on each SSE event, resets to zero on `notifications-read` DOM event (§5.3)
- [ ] Auto-reconnect on disconnect (handled within `<notification-badge>`)
- [ ] Tests for SSE event emission, delivery, reconnection, unread count endpoint

**Produces:** Admin sees real-time notification indicators.

**Depends on:** WP-10, WP-06

---

## WP-17: Notifications View

Persistent, browsable notification history — static HTML shell with Web Components fetching from a JSON API.

- [ ] Notifications JSON API endpoint (admin-protected): paginated list of notifications from DB (§5.5), cursor-based pagination; join against `following` table to include `is_following` boolean per actor on each notification item
- [ ] `<notification-list>` Web Component: fetches notifications JSON, renders the list, handles "load more" pagination
- [ ] `<notification-item>` Web Component: renders a single notification — composes `<actor-identity>` for the actor header and (on follow notifications) `<follow-button>`; `type` attribute (`follow` / `reply`) controls variant
- [ ] Follow notification items: include `<follow-button>` (not following back) or Unfollow text link (already following back) actionable inline (§5.5)
- [ ] Reply notification items: display reply content in a styled container within the notification row, with like/reply/boost action icons (§5.5)
- [ ] Mark-all-read API endpoint (`POST /admin/api/notifications/mark-all-read`): sets all `read = false` rows to `read = true`
- [ ] `<notification-list>` calls mark-all-read on init, then dispatches `notifications-read` on `document` to reset `<notification-badge>` (§5.5)
- [ ] Static HTML shell at `static/admin/notifications.html` that loads the Web Components
- [ ] Tests for notifications JSON API (auth, response format, pagination), mark-all-read endpoint, `notifications-read` event dispatch, follow-back action, inline reply content

**Produces:** Browsable notification history.

**Depends on:** WP-10, WP-06

---

## WP-18: Remaining Admin Views

Profile editing, social graph management, liked posts, search — all as static HTML shells with Web Components backed by JSON API endpoints.

- [ ] **WP-18 composite components** (build before the views that use them):
  - `<person-row>`: `<actor-identity>` + `<follow-button>` in a single row; used in the Following list, Followers list, and search results
  - `<profile-card>`: `<actor-banner mode="static">` + `<actor-identity>` + bio paragraph + `<follow-button>`; `mode` attribute (`modal` for overlay contexts, `public` for the standalone public profile page)
- [ ] **Profile view:** static HTML shell + Web Components + JSON API for reading and updating display name, bio, avatar, header image, links (§5.6, §8.2); uses `<actor-banner mode="editable">` at the top of the form (click banner or avatar to replace); bio is edited as Markdown source and rendered to HTML (with typographic processing) on save — the rendered HTML is used for the public profile page injection and for the `summary` field in the actor document; own published notes listed below the edit form using `<status-item own>` with Edit and Delete controls
- [ ] Banner and avatar upload: each handled via `<actor-banner mode="editable">`, uses media upload pipeline from WP-12
- [ ] **Following view:** static HTML shell + `<following-list>` Web Component + JSON API endpoint; each row rendered as `<person-row>` with Unfollow action
- [ ] **Followers view:** static HTML shell + `<followers-list>` Web Component + JSON API endpoint; each row rendered as `<person-row>` with Remove action (sends `Reject` or `Block`? — decide and document)
- [ ] **Likes view:** static HTML shell + `<likes-list>` Web Component + JSON API endpoint returning paginated liked posts; each item rendered as `<status-item>`
- [ ] **Search modal:** `<search-modal>` Web Component rendered as a modal overlay triggered from the nav search icon button; input field for `@user@domain`, calls JSON API to fetch remote actor via WebFinger; displays `<person-row>` result(s) on match, "no result" message on failure (§5.6)
- [ ] **Remote actor profile modal:** `<actor-profile-modal>` Web Component — a dark overlay containing a `<profile-card mode="modal">`; triggered by clicking any actor name, handle, or avatar in the admin interface (§5.6)
- [ ] **"Follow me" link on public profile page:** add to `static/pages/profile.html` as part of this pass if not already done in WP-04
- [ ] Tests for each JSON API endpoint and view

**Produces:** All admin views complete.

**Depends on:** WP-11, WP-12, WP-06

---

## Parallelism

Most packages are sequential due to their dependency chain, but some can proceed concurrently:

- **WP-06** (Auth) and **WP-04/05** (Actor/Signatures) are independent — both depend only on WP-02/03.
- **WP-07** (Home page) has no dependency on settings or auth — it can proceed as soon as WP-01 is complete, independent of everything else.
- **WP-12** (Media) depends only on WP-06, so it can proceed in parallel with WP-08 through WP-11.
- **WP-16** (SSE) and **WP-17** (Notifications view) are independent of each other — both need WP-10.
- **WP-18** (Remaining admin views) can start as soon as its dependencies are met; individual views within it are independent.

```
WP-01 ─┬→ WP-02 → WP-03 ─┬→ WP-04 → WP-05 → WP-08 → WP-09 → WP-10 → WP-11
        │                  │                                       │        │
        │                  ├→ WP-06 → WP-12                       ├→ WP-16 ├→ WP-18
        │                  │    │                                  │        │
        │                  │    ├→ WP-13 → WP-14                  └→ WP-17 │
        │                  │    │           │                               │
        │                  │    └→ WP-15 ←──┘                              │
        │                  │                                               │
        └→ WP-07           │                                               │
                           │                                               │
                           └── WP-12 ──────────────────────────────────────┘
```
