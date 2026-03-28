# CLAUDE.md

Instructions for Claude Code when working on this project. Read this file at the start of every session before writing any code. For full specifications, see `requirements.md`.

---

## What This Is

Tinker is a single-user ActivityPub microblog. One person posts short notes into the fediverse. There are no public post pages — the public site is a home page and a public profile page. Notes exist as AP objects on this server (JSON-LD only, `302 → /` for browsers). The admin is a timeline reader with compose, like, reply, and boost.

## Build Roadmap

The remaining work is broken into 18 ordered work packages in `TODO.md`. Read it at the start of each session to orient on what comes next. The parallelism diagram at the bottom of `TODO.md` shows which packages can proceed concurrently.

Key rule: complete the current work package (tests passing, ruff clean, mypy strict clean) before starting the next one.

## Route Structure

| Route           | Purpose                                      | Auth     | Rendering                              |
|-----------------|----------------------------------------------|----------|----------------------------------------|
| `/`             | Home page — static welcome/branding page     | No       | Self-contained static HTML (inline CSS/JS) |
| `/users/{actor}` | Public profile + AP actor endpoint           | No       | Self-contained static HTML (inline CSS/JS) for browsers; JSON-LD for AP consumers (content negotiation) |
| `/{actor}`      | Convenience redirect → `/users/{actor}`      | No       | 301 redirect                           |
| `/@{actor}`     | Mastodon-style redirect → `/users/{actor}`   | No       | 301 redirect                           |
| `/login`        | Login page                                   | No       | Static HTML page; styles from shared stylesheet (`/assets/css/styles.css`) |
| `/admin/*`      | Admin interface                              | Yes      | Jinja2 templates + Web Components + JSON API |

## Tech Stack

| Concern            | Technology                          | Notes                                        |
|--------------------|-------------------------------------|----------------------------------------------|
| Web framework      | Quart                               | Async Flask-compatible, native SSE support   |
| ORM                | SQLAlchemy 2.0 (async)              | Models, query building, async sessions       |
| Database           | SQLite via aiosqlite                | WAL mode enabled. File at `db/tinker.db`     |
| Migrations         | Alembic                             | Schema versioning, lives in `alembic/`       |
| HTTP client        | httpx                               | Async, HTTP/2, outbound federation requests  |
| Crypto             | cryptography                        | HTTP Signature signing/verification          |
| HTML sanitisation  | nh3                                 | Rust-based allowlist sanitiser               |
| Markdown           | markdown-it-py (or similar)         | Render note body to HTML                     |
| Image processing   | Pillow + pillow-heif                | Metadata stripping, optimisation, HEIC→JPEG  |
| Password hashing   | argon2-cffi                         | Not bcrypt                                   |
| Reverse proxy      | Caddy                               | TLS termination, static file serving         |
| Background tasks   | asyncio (in-process)                | No external queues or brokers                |
| Admin UI           | Jinja2 + Web Components + vanilla JS | Jinja2 templates in ``templates/admin/``, native Web Components, JSON API |
| Testing            | pytest + pytest-asyncio             |                                              |
| Linting/formatting | ruff                                |                                              |
| Type checking      | mypy (strict mode)                  |                                              |
| Dependency mgmt    | uv                                  | Never use pip directly                       |

## Project Structure

```
tinker/
├── app/
│   ├── __init__.py          # Quart app factory; blueprints, admin seed, CSP, HTTP client
│   ├── core/                # Config, database session, shared infrastructure
│   │   ├── config.py        # Env var loading, .env support, app constants
│   │   ├── database.py      # AsyncEngine, async_sessionmaker, PRAGMAs
│   │   ├── formatting.py    # Shared AP datetime formatting, handle derivation
│   │   └── http_client.py   # Process-level httpx.AsyncClient singleton (HTTP/2)
│   ├── models/              # 11 SQLAlchemy ORM models (Note, RemoteActor, Follower,
│   │                        #   Following, TimelineItem, Notification, DeliveryQueue,
│   │                        #   Settings, MediaAttachment, Like, Keypair)
│   ├── repositories/        # Data access layer (one per model)
│   ├── services/
│   │   ├── keypair.py       # RSA keypair generation and rotation
│   │   ├── note.py          # Note creation, editing, deletion; Markdown rendering
│   │   ├── remote_actor.py  # Remote actor fetching and caching (TTL: 24h)
│   │   └── settings.py      # Settings get/set with typed accessors; seeds defaults
│   ├── federation/          # ActivityPub protocol logic
│   │   ├── actor.py         # Actor document builder
│   │   ├── signatures.py    # HTTP Signature sign/verify (draft-cavage, RSA-SHA256)
│   │   ├── inbox.py         # Incoming activity processing (WP-10)
│   │   ├── outbox.py        # Outgoing activity creation & delivery (WP-08/09)
│   │   ├── delivery.py      # Fan-out, retry, dead instance detection (WP-09)
│   │   └── follow.py        # Outgoing Follow/Undo{Follow} service (WP-11)
│   ├── admin/               # Admin interface
│   │   ├── auth.py          # Login, logout, session, CSRF, rate limiting, require_auth, require_csrf
│   │   ├── routes.py        # Auth-gated admin page routes (/admin/*)
│   │   ├── api.py           # JSON API endpoints (WP-13+)
│   │   └── sse.py           # Server-Sent Events for notification push (WP-16)
│   ├── public/              # Public routes
│   │   └── routes.py        # /{username}, WebFinger, NodeInfo, /login served by auth bp
│   └── media.py             # Upload handling, metadata stripping, avatar proxying (WP-12)
├── static/                  # Served at /assets/ (static_url_path="/assets")
│   ├── css/
│   │   └── styles.css       # Shared stylesheet: OKLCH palette, light-dark() tokens,
│   │                        #   color-mix() shading, Inter font-face, reset, login styles
│   ├── fonts/               # Inter variable font (woff2, regular + italic)
│   ├── pages/               # Public HTML pages (link to shared stylesheet)
│   │   ├── home.html        # Home page / (WP-07)
│   │   ├── profile.html     # Public profile page (/{actor}), browser view
│   │   └── login.html       # Login page (/login)
│   ├── admin/               # Empty — admin shells moved to templates/admin/ (ADR-001)
│   └── js/
│       └── components/      # Web Components (Custom Elements) — admin only (WP-13+)
├── templates/
│   └── admin/               # Jinja2 admin shell templates
│       ├── base.html        # Shared base: <head>, <nav-bar>, foundation scripts
│       ├── timeline.html    # Home / timeline view
│       ├── notifications.html
│       ├── profile.html
│       ├── following.html
│       ├── followers.html
│       └── likes.html
├── db/                      # SQLite database file (tinker.db)
├── media/                   # Uploaded images (optimised) + cached avatars
├── tests/
│   ├── unit/                # test_app_factory, test_auth, test_config, test_database,
│   │                        #   test_follow_builders, test_keypair_service,
│   │                        #   test_remote_actor_service, test_repositories,
│   │                        #   test_settings_service, test_signatures
│   └── integration/         # test_auth_routes, test_follow, test_public_routes,
│                            #   test_signature_refetch, test_note_routes,
│                            #   test_delivery_pipeline, test_inbox
├── alembic/
│   └── versions/            # 001_initial_schema — all 11 tables
├── docs/
│   └── adr/                 # Architecture Decision Records
├── REQUIREMENTS.md          # Full functional spec
├── TODO.md                  # Ordered work packages
├── CLAUDE.md                # This file
└── pyproject.toml
```

Caddy and systemd configs live outside `tinker/` — they are system-level concerns.

---

## Key Architectural Decisions

1. **No HTML post pages.** AP object URIs (e.g., `/notes/{id}`) return JSON-LD for federation consumers and `302 → /` for browsers. Every note must still be fetchable at its AP URI — federation breaks without this.

2. **In-process async, no external queues.** Background work uses `asyncio.create_task()`. Crash recovery: tasks are persisted to `ap_delivery_queue` in SQLite before dispatch; on startup, incomplete tasks are re-enqueued.

3. **SQLAlchemy 2.0 async with aiosqlite.** The ORM handles model definitions, query building, and write serialisation through its async session. Combined with WAL mode and `busy_timeout=5000`, this handles SQLite's single-writer constraint without a custom write queue.

4. **Alembic with sync engine.** Alembic's `--autogenerate` requires a synchronous engine. The `env.py` must create a sync `Engine` from the same DB path using `sqlite:///` (not `sqlite+aiosqlite://`). The async engine is for the running app; the sync engine is for migration tooling only.

5. **Request-scoped sessions.** Create a new `AsyncSession` per request via `before_request`, store on `g.db_session`, close in `teardown_appcontext`. Background tasks (`asyncio.create_task()`) must create their own sessions from the `async_sessionmaker` — never share sessions across request/task boundaries.

6. **Repository pattern for all database access.** Keeps the service layer testable in isolation. Repository tests use fixtures and mocks — they do not hit the database.

7. **Three-layer configuration.**
   - **`app/core/config.py`** — App-level constants that are fixed at build time and do not vary between deployments: `USER_AGENT`, `PAGE_SIZE`, `OUTBOX_PAGE_SIZE`, `COLLECTION_PAGE_SIZE`, and `make_actor_uri()`. Add new application constants here, not in `.env`. Import these in modules rather than defining local duplicates.
   - **Environment variables / `.env`** — Infrastructure values that differ between deployments: domain, database path, media path, secret key, username, admin password. These are loaded at startup by `load_config()` and are immutable at runtime. `.env` is for deployment-specific values only — never for application constants.
   - **Database settings table** — Identity and content editable at runtime through the admin UI: display name, bio, avatar, links. See `requirements.md` §8.

8. **Jinja2 for admin shells; string interpolation for public pages.** Admin views (`/admin/*`) are rendered by Jinja2 templates that live in `templates/admin/`. A shared base template (`templates/admin/base.html`) owns the `<head>`, `<nav-bar>`, and foundation `<script>` tags; each view extends it and overrides `{% block title %}`, `{% block main %}`, and `{% block scripts %}`. Jinja2's HTML auto-escaping is enabled by default for `.html` templates, which protects against XSS in injected user values (display name, handle, avatar, CSRF token). The `/{actor}` public profile page is the exception: it still uses simple string interpolation (not Jinja2) to embed display name, bio, avatar, handle, and links at serve time. Public pages do not use Jinja2 — see ADR-001. Admin pages load Web Components (Custom Elements) which fetch data from JSON API endpoints. JS is vanilla only — no framework, no bundler, no TypeScript. Pages should remain readable without JavaScript; interactivity is a progressive enhancement.

9. **`/users/{actor}` is dual-purpose.** The actor route at `/users/{actor}` serves both as the public profile page (HTML for browsers) and the ActivityPub actor endpoint (JSON-LD for federation consumers). Content negotiation on `Accept` header determines the response. This is the canonical URI used in WebFinger and federation. Convenience redirects exist at `/{actor}` and `/@{actor}` (Mastodon-style) for human visitors.

10. **Timeline polls, notifications push.** The admin timeline refreshes via polling (e.g., every 30s) against a JSON API endpoint. SSE is used only for notification events (likes, boosts, follows, replies). The inbox processing pipeline emits to an `asyncio.Queue`; the SSE endpoint reads from it.

11. **Signature verification with re-fetch.** Try cached public key first. On failure, fetch the actor document fresh and retry once. Handles remote key rotation gracefully.

12. **Auto-accept follows.** No moderation queue. Send `Accept{Follow}` immediately.

13. **Mastodon-first.** When the AP spec is ambiguous, match Mastodon's behaviour.

14. **No edit history.** Edits overwrite. `updated_at` is tracked for future use.

15. **Avatar proxying.** Never render remote avatar URLs directly. Fetch to local storage, serve from `/media/avatars/`.

---

## ActivityPub / Federation

Tinker implements ActivityPub for federation with the Fediverse. The W3C spec is underspecified in practice—Mastodon is the de facto standard. When the spec and Mastodon's behavior diverge, match Mastodon.

### Authoritative References

- **Primary:** Mastodon's ActivityPub documentation: https://docs.joinmastodon.org/spec/activitypub/
- **Secondary:** W3C ActivityPub spec: https://www.w3.org/TR/activitypub/
- **HTTP Signatures:** draft-cavage-http-signatures-12 specifically (NOT later drafts, NOT RFC 9421)
- **Survey of fediverse compliance:** https://swicg.github.io/activitypub-http-signature/

When in doubt about how an activity or object should be structured, check what Mastodon sends/expects rather than what the W3C spec says is valid.

### HTTP Signatures

**Never implement signature construction or verification manually. Use `apsig`** (`apsig.draft.Signer` / `Verifier`). It implements draft-cavage-http-signatures-12 and uses the `cryptography` library internally. See `app/federation/signatures.py` for the project's implementation.

#### Critical signature rules

- The body MUST be passed as raw bytes. If you serialize JSON, sign the exact byte string you send. Re-serializing (e.g., parsing then re-dumping) will change key order or whitespace and break the digest.
- Use RSA 2048-bit or larger keys. Ed25519 is not yet widely supported in the fediverse.
- The `keyId` in outbound signatures must be `{actor_id}#main-key` — this must match the `publicKey.id` field in your actor document exactly.
- When verifying inbound signatures, you must fetch the sender's actor document to retrieve their public key. Cache aggressively but handle key rotation: if verification fails with a cached key, re-fetch the actor and retry once.
- Handle `Delete` activities from actors whose profiles no longer exist — signature verification will fail because the public key can't be fetched. Log and discard gracefully; do not retry or error-loop.

### @context Array

Use this exact `@context` for actor documents and activities. Do not construct your own from the spec.

```python
ACTIVITYPUB_CONTEXT = [
    "https://www.w3.org/ns/activitystreams",
    "https://w3id.org/security/v1",
]
```

Do not expand security terms into full namespace URIs (e.g., `https://w3id.org/security#publicKey`). Mastodon expects the compact form (`publicKey`) and will fail to retrieve keys if you use the expanded form.

If you need Mastodon-specific extensions (hashtags, sensitive flags, featured collections), add the Mastodon context. Refer to https://docs.joinmastodon.org/spec/activitypub/ for the current shape.

### Content-Type Negotiation

Every endpoint that serves ActivityPub data must handle content negotiation correctly.

| Endpoint | Accept header check | Response Content-Type |
|---|---|---|
| Actor (`/users/{username}`) | `application/activity+json` or `application/ld+json` | `application/activity+json` |
| WebFinger (`/.well-known/webfinger`) | N/A (always JSON) | `application/jrd+json` |
| Inbox (POST) | N/A (receiving) | N/A |
| Outbox | `application/activity+json` or `application/ld+json` | `application/activity+json` |

- If the `Accept` header requests HTML (or has no ActivityPub type), serve the HTML page. If it requests `application/activity+json` or includes `application/ld+json; profile="https://www.w3.org/ns/activitystreams"`, serve the JSON-LD actor/object.
- **Caddy/proxy configuration:** Set `Vary: Accept` on all content-negotiated responses (the `/users/{actor}` endpoint). Without this, a cached HTML response may be served to a Mastodon fetch, or vice versa. This is a silent failure — no errors, just invisible breakage.
- Returning `application/json` instead of `application/activity+json` will make your actor undiscoverable on Mastodon with no error message.

### WebFinger

WebFinger must be served at `/.well-known/webfinger` and respond to `?resource=acct:{username}@{domain}`.

```python
# Response structure
{
    "subject": "acct:{username}@{domain}",
    "aliases": [
        "https://{domain}/users/{username}"
    ],
    "links": [
        {
            "rel": "self",
            "type": "application/activity+json",
            "href": "https://{domain}/users/{username}"
        },
        {
            "rel": "http://webfinger.net/rel/profile-page",
            "type": "text/html",
            "href": "https://{domain}/users/{username}"
        }
    ]
}
```

#### WebFinger rules

- The `subject` must use the `acct:` URI scheme.
- The `self` link `href` must point to the URL where the actor JSON-LD document is actually served. Mastodon uses this URL to fetch the actor.
- The actor document's `id` field must match the URL where it's served. If the `id` says `https://example.com/users/cam` but the document lives at `https://example.com/ap/users/cam`, Mastodon will reject it.
- Mastodon performs a second WebFinger lookup on the domain extracted from the actor's `id`. If your WebFinger domain and actor `id` domain don't match, discovery fails silently.
- `preferredUsername` in the actor document must correspond to the WebFinger `acct:` username.
- Content-Type must be `application/jrd+json`.

### Actor Document

Minimal actor document that Mastodon will accept:

```python
{
    "@context": [
        "https://www.w3.org/ns/activitystreams",
        "https://w3id.org/security/v1"
    ],
    "id": "https://{domain}/users/{username}",
    "type": "Person",
    "preferredUsername": "{username}",
    "name": "{display_name}",
    "summary": "{bio_html}",
    "inbox": "https://{domain}/users/{username}/inbox",
    "outbox": "https://{domain}/users/{username}/outbox",
    "followers": "https://{domain}/users/{username}/followers",
    "following": "https://{domain}/users/{username}/following",
    "publicKey": {
        "id": "https://{domain}/users/{username}#main-key",
        "owner": "https://{domain}/users/{username}",
        "publicKeyPem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
    }
}
```

- The `id` must be the canonical URL of this document (self-referencing).
- `publicKey.id` must be `{actor_id}#main-key`.
- `publicKey.owner` must match the actor `id`.
- If `type` is `Application` or `Service`, Mastodon flags the account as a bot.
- You must serve an outbox endpoint even if it's empty — some implementations require it for discovery.

### Inbox Handling

- Return `202 Accepted` for valid incoming activities. Process asynchronously.
- Verify HTTP signatures on all incoming POST requests before processing.
- Expect and handle: `Follow`, `Undo` (Follow, Like, Announce), `Accept`, `Reject`, `Create` (Note), `Update`, `Delete`, `Like`, `Announce`.
- `Delete` activities will arrive for actors whose profiles no longer exist. The public key will be unfetchable. Handle gracefully — log and discard.
- Set a recursion depth limit when resolving referenced objects. Unbounded recursion is a DoS vector.
- Do not fetch OpenGraph previews for links in received posts — or if you do, rate-limit and deduplicate. Every instance that receives a post independently fetches link previews, which can DDoS the linked server.

### Outbound Delivery

- Deliver to each follower's inbox (or shared inbox if available) via signed POST.
- Implement retry with exponential backoff. Federation is inherently flaky — single-attempt delivery will lose messages.
- For `Create` activities (new posts), wrap the object in a `Create` activity with a unique `id`.
- For `Update` activities, the `updated` timestamp on the object MUST change or Mastodon will silently drop the update.
- Deduplicate delivery — if multiple followers share a `sharedInbox`, POST once.

### Testing and Debugging

- **activitypub.academy** — Creates anonymous Mastodon accounts for testing. Provides server-side logs of what it sends and receives. Essential for debugging silent failures.
- **verify.funfedi.dev** — Actor document validator.
- **ngrok or similar** — Required for local development. You cannot test federation against localhost because remote servers need to reach your inbox and fetch your actor document.
- **Log everything.** ActivityPub failures are mostly silent (202 Accepted, content never appears). Log all inbound requests, all outbound delivery attempts and responses, and all signature verification results.

### Common Failure Modes

| Symptom | Likely cause |
|---|---|
| Actor not discoverable on Mastodon | Wrong Content-Type on actor endpoint or WebFinger; `id` / WebFinger domain mismatch; missing outbox endpoint |
| 202 Accepted but content never appears | HTTP signature invalid; `@context` uses expanded namespace URIs; actor `id` doesn't match served URL |
| Updates not propagating | Missing or unchanged `updated` timestamp on the object |
| Signature verification failures on inbound | Cached stale public key (handle key rotation); `Delete` from removed actor (discard gracefully) |
| Followers not receiving posts | Delivery not retrying on failure; shared inbox deduplication missing; outbound signature malformed |
| Profile fields not rendering on Mastodon | Using `https://schema.org/` context instead of `http://schema.org#` (Mastodon's known bug — match their expectation) |

---

## Commands

Reference these exact commands — never infer or abbreviate.

- Install dependencies: `uv sync`
- Install with dev dependencies: `uv sync --group dev`
- Run development server: `uv run quart run --reload`
  - `QUART_APP` and `QUART_DEBUG` are set in `.env` and loaded automatically.
  - `--reload` watches `.py` files and restarts on change. With `watchfiles` installed (it is, as a dev dependency), Werkzeug uses it automatically for faster, OS-native file event detection instead of polling.
  - `QUART_DEBUG=true` enables detailed error pages and bypasses the in-memory HTML template caches, so changes to files in `static/pages/` are reflected on the next request without a restart.
  - Static assets (CSS, JS, fonts) are always read from disk on each request in dev — no restart needed for those changes.
- Run tests: `uv run pytest`
- Run tests with coverage: `uv run pytest --cov=app --cov-report=term-missing`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- Type check: `uv run mypy .`
- Run all checks: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest`
- Create Alembic migration: `uv run alembic revision --autogenerate -m "description"`
- Run migrations: `uv run alembic upgrade head`
- Rollback migration: `uv run alembic downgrade -1`

---

## Non-Negotiables

These standards are not aspirational — they are baseline expectations for every contribution. Every line of code must meet them.

### Tests

- Every feature ships with tests. No exceptions.
- Framework: `pytest` + `pytest-asyncio`.
- Test structure mirrors source structure: `tests/unit/`, `tests/integration/`.
- Repository layer tests must not hit the database — use fixtures and mocks.
- Tests must cover edge cases and error conditions, not just the happy path.

**Federation integration tests:** Use a mock AP server (a minimal Quart app running in the test process). See `requirements.md` §11 for the test scenarios. The mock server acts as a remote ActivityPub instance with an actor document, inbox recorder, and WebFinger endpoint.

### Documentation

- All public functions, classes, and modules have docstrings (enforced by `ruff` with pydocstyle rules).
- Type hints on everything — no `Any` unless explicitly justified with a comment.
- When code changes existing behaviour, update the corresponding documentation to match — stale documentation is a bug.
- Significant architectural decisions are recorded as ADRs in `docs/adr/`.

### Code Quality

- `ruff` for linting and formatting.
- `mypy` in strict mode — configured from day one, not retrofitted.
- Project structure enforces layer separation: routes → services → repositories → models.
- The repository pattern for all database access keeps the service layer testable in isolation.
- Code should be readable and maintainable first, clever second.

### Error Handling

- Errors must be handled gracefully and predictably.
- Error messages must give future maintainers enough information to understand what went wrong and why.
- Swallowing exceptions or returning silent failures is not acceptable.
- Background task failures (delivery, actor fetch) must be logged with enough context to diagnose without reproducing.

### Simplicity and Design

- Code should do only what is needed, expressed as simply as possible — optimise for human readability and future maintainability.
- Write code that affords future change without over-engineering for changes that may never come (YAGNI): avoid both code so rigid it makes future changes unnecessarily hard, and code so speculative it adds complexity without current value.
- Apply the "ilities" appropriate to the class of software being developed — accessibility, testability, reliability, security, maintainability, observability — and meet them as part of the work, not as a later consideration.

### Security

- Passwords hashed with `argon2-cffi` — not bcrypt.
- Session tokens in HTTPOnly, Secure, SameSite=Strict cookies — never localStorage.
- CSRF protection via token on all state-changing endpoints. Admin JSON API endpoints use the `@require_csrf` decorator which validates the `X-CSRF-Token` header; form endpoints validate via `validate_csrf()`.
- Content Security Policy on all HTML responses. Admin pages use a strict CSP with a per-request nonce for the inline CSRF bootstrap script (`script-src 'self' 'nonce-...'`); public pages use `script-src 'self'`. All responses include `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY`.
- Rate limiting on the login endpoint and the ActivityPub inbox, with hard caps (10k entries) on the in-memory rate-limit dicts to prevent memory exhaustion.
- All database access via SQLAlchemy ORM — no raw SQL string construction.
- All inbound ActivityPub content sanitised with `nh3` before storage and display.
- Remote avatar URLs never rendered directly in `<img>` tags — proxy through local storage.
- Media uploads validated (MIME type, file size) before storage.

### Boundaries

**Always — proceed without asking:**

- Run `ruff`, `mypy`, and `pytest` before committing.
- Follow Conventional Commits format for all commit messages.
- Write docstrings on all public interfaces.
- Use UUIDs for all primary keys.
- Use the repository pattern for all database access.
- Write tests alongside the feature code, not after.

**Ask first — pause and confirm before proceeding:**

- Modifying the database schema (new Alembic migrations).
- Adding new dependencies to `pyproject.toml`.
- Changing the project directory structure.
- Modifying authentication or session handling logic.
- Changing the ActivityPub object schema or federation behaviour.
- Any change to the Alembic migration history.

**Never — hard stops under any circumstances:**

- Commit secrets, API keys, or credentials.
- Use `localStorage` or `sessionStorage` for any auth-related data.
- Construct raw SQL strings — use SQLAlchemy ORM exclusively.
- Remove or skip a failing test without explicit approval.
- Modify files outside of the project directory (Caddy config, systemd units, etc.).
- Add external message brokers, task queues, or caching layers.
- Add a JS build step, bundler, or SPA framework to the admin UI.

---

## What Good Looks Like

Before marking any feature complete, verify:

1. Tests written and passing.
2. `ruff` passes with no warnings, `mypy` passing in strict mode.
3. If the decision was non-obvious, an ADR exists in `docs/adr/`.
4. This file (`CLAUDE.md`) updated if the change affects architecture, conventions, or project structure.

---

## Development Environment

- Use `uv` for all dependency management — never `pip` directly.
- Dev dependencies (pytest, mypy, ruff, etc.) in the `dev` dependency group in `pyproject.toml`.
- `uv sync` for reproducible installs on both local and server.
- Local dev mirrors production: Quart app + real SQLite DB at `db/tinker.db`. No database mocks in development — use a local dev database.
- Caddy runs in front of Quart in production; in development, connect directly to the Quart dev server.
- Never mock the database in development — use a local dev database seeded with fixture data.
- A `.env` file in the project root provides environment variables for local dev. It must be in `.gitignore`.
- Required environment variables (see `requirements.md` §8.1 for full list):
  - `TINKER_DOMAIN`, `TINKER_USERNAME`, `TINKER_SECRET_KEY`, `TINKER_DB_PATH`, `TINKER_MEDIA_PATH`
  - `TINKER_ADMIN_PASSWORD` — plaintext password set on first run; hashed with argon2 and stored in the settings table. Subsequent starts with the same value set are no-ops once the hash is persisted.
- Static files in `static/` are served at the `/assets/` URL prefix (e.g. `static/css/styles.css` → `/assets/css/styles.css`). This is configured via `static_url_path="/assets"` on the Quart app instance.

---

## Git Workflow

**Branching**

- `main` is always deployable — never commit directly to it.
- All work on feature branches using prefixes: `feature/`, `fix/`, `chore/`, `docs/`.
- Examples: `feature/inbox-processing`, `fix/signature-verification`, `chore/update-dependencies`.

**Commit messages**

Use Conventional Commits format:

- `feat:` — new feature
- `fix:` — bug fix
- `chore:` — maintenance, dependency updates, tooling
- `docs:` — documentation only
- `test:` — adding or updating tests
- `refactor:` — code change that neither fixes a bug nor adds a feature

**Commit size**

- Commit frequently in small logical units — do not accumulate large changesets.
- Each commit should represent one coherent, reviewable change.

**Merging**

- Merge feature branches to `main` via squash commit.
- Verify tests pass before merging.

---

## Conventions

### IDs

UUIDs for all primary keys. Sequential IDs leak content volume and are incompatible with ActivityPub object URLs.

### Deletes

Hard deletes only — no soft delete or archive pattern. When a note is deleted locally, a `Delete` activity with `Tombstone` is sent to followers.

### Notes

Note body text is Markdown source rendered to HTML. Typographic processing must be applied at render time, following the rules of a utility like SmartyPants:

- Straight quotes (`"`, `'`) → curly/smart quotes (`"…"`, `'…'`)
- `--` → en dash (–); `---` → em dash (—)
- `...` → ellipsis (…)

The rendered HTML stored in `body_html` must contain the typographically processed output. Raw Markdown source is preserved separately in `body` for editing and federation.

### Media

- Uploaded to the configured media directory.
- Pillow strips all metadata (EXIF, IPTC, XMP) and optimises images at upload time. Single output file per upload — no derivatives.
- `pillow-heif` handles HEIC uploads, converting to JPEG.
- Avatars from remote actors proxied through local storage at `/media/avatars/`.

### Database Migrations

Managed via Alembic. Migration files live in `alembic/`. Applied on startup or as part of the deployment process.

### Content Types

- AP endpoints: `application/activity+json; charset=utf-8`.
- `/users/{actor}` route: content negotiation — JSON-LD for AP consumers, HTML for browsers.
- Note URIs (`/notes/{id}`): JSON-LD for AP consumers, `302 → /` for browsers.
- All other pages: standard `text/html`.

### Public Pages

- Home page (`/`), public profile (`/users/{actor}`), and login page (`/login`) are static HTML pages. They may link to external stylesheets, JavaScript files, and Web Components served from `/assets/` — there is no requirement to inline assets.
- The `/users/{actor}` profile page has its content (display name, bio, avatar, handle, links) injected server-side via simple string interpolation from the settings table — not Jinja2. (Admin pages use Jinja2; public pages do not. See ADR-001.)
- Pages must not require JavaScript to display their core content; JS is a progressive enhancement only.
- The shared stylesheet (`static/css/styles.css`, served at `/assets/css/styles.css`) owns the OKLCH color palette, semantic `light-dark()` tokens, `color-mix()` shade derivation, Inter `@font-face` declarations, the box-model reset, and page-specific component styles (e.g. `.login-form`). Prefer adding new component styles here rather than in `<style>` blocks.

### CSS

- Use **native CSS nesting** throughout — selectors, pseudo-classes, pseudo-elements, and media queries should be nested inside their parent rule rather than written as separate flat rules.
- **BEM blocks are a single nested rule.** All element (`__`) and modifier (`--`) selectors for a BEM block belong nested inside that block's rule, not written as separate flat selectors. A component's entire style surface should be readable as one coherent block.
- Use **logical properties** (`inline-size`, `block-size`, `padding-inline`, `margin-block-end`, `text-align: start/end`, etc.) in preference to physical equivalents (`width`, `height`, `padding-left`, `margin-bottom`, `text-align: left/right`, etc.).
- Use `::before` / `::after` (double colon) for pseudo-elements.
- **Custom elements need explicit `display`**. Browsers treat unknown/custom elements as `display: inline` by default. Any Web Component that participates in block, flex, or grid layout must have an explicit `display` value set in the stylesheet (typically `display: block`). Without this, percentage-based sizes inside the element resolve against the element's shrunken content width rather than the viewport, causing silent layout breakage.
- **Never suppress focus rings globally.** Do not set `outline: 0` or `outline: none` on broad selectors such as `input:focus` or `*:focus` without providing a visible replacement. Removing focus indicators globally is a WCAG 2.4.7 violation. Components that require custom focus treatment should style it explicitly on their own focused selectors; everything else should keep the browser default.

### Admin UI

- Jinja2 templates in `templates/admin/`. `base.html` owns all shared boilerplate; each view template extends it with `{% block title %}`, `{% block main %}`, and `{% block scripts %}` only. The base template includes a CSP-nonced inline `<script>` that sets `window.__TINKER__.csrf` for Web Component use.
- Jinja2 HTML auto-escaping is on by default — never use `| safe` on user-supplied values.
- Add new admin views by creating a new child template and a one-line `render_template()` call in `app/admin/routes.py`. No other files need to change.
- Web Components (Custom Elements) for reusable UI pieces (`<timeline-item>`, `<compose-box>`, `<notification-badge>`, etc.).
- All admin data fetched via JSON API endpoints (under `app/admin/api.py`).
- Vanilla JS only. No framework, no bundler, no TypeScript. JS files served from `static/js/`.
- Pages should be readable without JS even if interactions require it (progressive enhancement where feasible).

### HTTP Client

- All outbound HTTP requests (federation delivery, actor fetches, avatar proxying, WebFinger lookups) use a shared process-level `httpx.AsyncClient` singleton managed by `app/core/http_client.py`.
- The client is initialised at app startup (`init_http_client()`) and disposed on shutdown (`close_http_client()`). HTTP/2 is enabled via the `httpx[http2]` extra.
- Never create per-request `httpx.AsyncClient` instances — use `get_http_client()` to obtain the shared client.
- The shared client has a default `User-Agent` header and `follow_redirects=True`; callers only need to set endpoint-specific headers like `Accept`.

---

## Deployment

- Push to `main` → SSH into server → `git pull` → `uv sync` → Alembic migrations → restart Quart via systemd.
- Caddy handles TLS, HTTPS, and proxying to the Quart process.
- Caddy and systemd configs are system-level — not tracked in the project repo.
- For backups: `sqlite3 db/tinker.db ".backup /path/to/backup/tinker-YYYY-MM-DD.db"` before any deployment.

---

## Architecture Decision Records

When making a non-obvious architectural decision, create an ADR in `docs/adr/` using this format:

```markdown
# ADR-NNN: [Title]

## Date
YYYY-MM-DD

## Status
Accepted | Superseded by ADR-NNN

## Context
What problem were we solving?

## Decision
What did we decide?

## Alternatives Considered
What else did we evaluate and why did we reject it?

## Consequences
What does this decision make easier or harder?
```

---

## Out of Scope

No Webmention, no Article type, no edit history, no polls, no custom emoji, no Move activity, no multi-user, no external queues, no private archive view, no SPA framework. Jinja2 is used for admin shell templates (ADR-001); public pages use plain string interpolation and do not go through Jinja2. See `requirements.md` §13.
