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
| `/{actor}`      | Public profile + AP actor endpoint           | No       | Self-contained static HTML (inline CSS/JS) for browsers; JSON-LD for AP consumers (content negotiation) |
| `/login`        | Login page                                   | No       | Static HTML page; styles from shared stylesheet (`/assets/css/styles.css`) |
| `/admin/*`      | Admin interface                              | Yes      | Static HTML shells + Web Components + JSON API |

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
| Admin UI           | Static HTML + Web Components + vanilla JS | Static HTML shells, native Web Components, JSON API |
| Testing            | pytest + pytest-asyncio             |                                              |
| Linting/formatting | ruff                                |                                              |
| Type checking      | mypy (strict mode)                  |                                              |
| Dependency mgmt    | uv                                  | Never use pip directly                       |

## Project Structure

```
tinker/
├── app/
│   ├── __init__.py          # Quart app factory; registers blueprints, seeds admin password
│   ├── core/                # Config, database session, middleware
│   │   ├── config.py        # Env var loading, .env support
│   │   └── database.py      # AsyncEngine, async_sessionmaker, PRAGMAs
│   ├── models/              # 11 SQLAlchemy ORM models (Note, RemoteActor, Follower,
│   │                        #   Following, TimelineItem, Notification, DeliveryQueue,
│   │                        #   Settings, MediaAttachment, Like, Keypair)
│   ├── repositories/        # Data access layer (one per model)
│   ├── services/
│   │   ├── keypair.py       # RSA keypair generation and rotation
│   │   ├── remote_actor.py  # Remote actor fetching and caching (TTL: 24h)
│   │   └── settings.py      # Settings get/set with typed accessors; seeds defaults
│   ├── federation/          # ActivityPub protocol logic
│   │   ├── actor.py         # Actor document builder
│   │   ├── signatures.py    # HTTP Signature sign/verify (draft-cavage, RSA-SHA256)
│   │   ├── inbox.py         # Incoming activity processing (WP-10)
│   │   ├── outbox.py        # Outgoing activity creation & delivery (WP-08/09)
│   │   └── delivery.py      # Fan-out, retry, dead instance detection (WP-09)
│   ├── admin/               # Admin interface
│   │   ├── auth.py          # Login, logout, session, CSRF, rate limiting, require_auth
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
│   ├── admin/               # Static HTML shells for admin views (WP-13+)
│   └── js/
│       └── components/      # Web Components (Custom Elements) — admin only (WP-13+)
├── db/                      # SQLite database file (tinker.db)
├── media/                   # Uploaded images (optimised) + cached avatars
├── tests/
│   ├── unit/                # test_app_factory, test_auth, test_config, test_database,
│   │                        #   test_keypair_service, test_remote_actor_service,
│   │                        #   test_repositories, test_settings_service, test_signatures
│   └── integration/         # test_auth_routes, test_public_routes, test_signature_refetch
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

7. **Two-layer configuration.** Environment variables for infrastructure (domain, paths, secrets) — loaded at startup, immutable. Database settings table for identity and content (display name, bio, avatar, links) — editable through the admin at runtime. See `requirements.md` §8.

8. **Shared assets, no server-side template engine.** All pages — public and admin alike — may link to external stylesheets, JavaScript files, and Web Components served from `/assets/`. There is no server-side template engine; any dynamic content is injected via simple string interpolation before serving. The `/{actor}` profile page uses this approach to embed display name, bio, avatar, handle, and links from the settings table. Admin pages (`/admin/*`) are static HTML shells that load Web Components (Custom Elements) which fetch data from JSON API endpoints. JS is vanilla only — no framework, no bundler, no TypeScript. Pages should remain readable without JavaScript; interactivity is a progressive enhancement.

9. **`/{actor}` is dual-purpose.** The actor route serves both as the public profile page (HTML for browsers) and the ActivityPub actor endpoint (JSON-LD for federation consumers). Content negotiation on `Accept` header determines the response. This is the same URI used in WebFinger and federation.

10. **Timeline polls, notifications push.** The admin timeline refreshes via polling (e.g., every 30s) against a JSON API endpoint. SSE is used only for notification events (likes, boosts, follows, replies). The inbox processing pipeline emits to an `asyncio.Queue`; the SSE endpoint reads from it.

11. **Signature verification with re-fetch.** Try cached public key first. On failure, fetch the actor document fresh and retry once. Handles remote key rotation gracefully.

12. **Auto-accept follows.** No moderation queue. Send `Accept{Follow}` immediately.

13. **Mastodon-first.** When the AP spec is ambiguous, match Mastodon's behaviour.

14. **No edit history.** Edits overwrite. `updated_at` is tracked for future use.

15. **Avatar proxying.** Never render remote avatar URLs directly. Fetch to local storage, serve from `/media/avatars/`.

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
- CSRF protection via token on all state-changing endpoints.
- Rate limiting on the login endpoint and the ActivityPub inbox.
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

### Media

- Uploaded to the configured media directory.
- Pillow strips all metadata (EXIF, IPTC, XMP) and optimises images at upload time. Single output file per upload — no derivatives.
- `pillow-heif` handles HEIC uploads, converting to JPEG.
- Avatars from remote actors proxied through local storage at `/media/avatars/`.

### Database Migrations

Managed via Alembic. Migration files live in `alembic/`. Applied on startup or as part of the deployment process.

### Content Types

- AP endpoints: `application/activity+json; charset=utf-8`.
- `/{actor}` route: content negotiation — JSON-LD for AP consumers, HTML for browsers.
- Note URIs (`/notes/{id}`): JSON-LD for AP consumers, `302 → /` for browsers.
- All other pages: standard `text/html`.

### Public Pages

- Home page (`/`), public profile (`/{actor}`), and login page (`/login`) are static HTML pages. They may link to external stylesheets, JavaScript files, and Web Components served from `/assets/` — there is no requirement to inline assets.
- The `/{actor}` profile page has its content (display name, bio, avatar, handle, links) injected server-side via simple string interpolation from the settings table — not a template engine.
- Pages must not require JavaScript to display their core content; JS is a progressive enhancement only.
- The shared stylesheet (`static/css/styles.css`, served at `/assets/css/styles.css`) owns the OKLCH color palette, semantic `light-dark()` tokens, `color-mix()` shade derivation, Inter `@font-face` declarations, the box-model reset, and page-specific component styles (e.g. `.login-form`). Prefer adding new component styles here rather than in `<style>` blocks.

### CSS

- Use **native CSS nesting** throughout — selectors, pseudo-classes, pseudo-elements, and media queries should be nested inside their parent rule rather than written as separate flat rules.
- Use **logical properties** (`inline-size`, `block-size`, `padding-inline`, `margin-block-end`, `text-align: start/end`, etc.) in preference to physical equivalents (`width`, `height`, `padding-left`, `margin-bottom`, `text-align: left/right`, etc.).
- Use `::before` / `::after` (double colon) for pseudo-elements.

### Admin UI

- Static HTML shells served from `static/admin/`. Each view is a minimal HTML page that loads Web Components.
- Web Components (Custom Elements) for reusable UI pieces (`<timeline-item>`, `<compose-box>`, `<notification-badge>`, etc.).
- All admin data fetched via JSON API endpoints (under `app/admin/api.py`).
- Vanilla JS only. No framework, no bundler, no TypeScript. JS files served from `static/js/`.
- Pages should be readable without JS even if interactions require it (progressive enhancement where feasible).

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

No Webmention, no Article type, no edit history, no polls, no custom emoji, no Move activity, no multi-user, no external queues, no private archive view, no SPA framework, no server-side template engine. See `requirements.md` §13.