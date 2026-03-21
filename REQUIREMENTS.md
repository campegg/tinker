# Tinker: an ActivityPub Microblog — Functional Requirements

**Version:** 3.0
**Date:** 2026-03-19

-----

## 1. Project Overview

A single-user microblog that operates as a federated ActivityPub node. Notes are published into the fediverse — no individual post pages exist on the site itself. The public web presence consists of a home page and a public profile page. The admin interface is a real-time ActivityPub timeline reader with interaction capabilities (like, reply, boost) and a compose box for publishing notes.

### 1.1 Design Principles

- **Minimal public surface:** A home page, a public profile page, no post archive, no individual post URLs rendered as HTML. Content lives in the fediverse — this server is a publishing node, not a reading destination.
- **Fediverse-native content:** Notes exist as ActivityPub objects. Remote instances are the durable, human-readable copies. This is intentional — the ephemeral local footprint is a feature, not a limitation.
- **Minimal infrastructure:** No external message brokers, task queues, or caching layers. SQLite for persistence, in-process async for background work. Library dependencies (SQLAlchemy, Pillow, etc.) are fine — it's operational infrastructure that should stay minimal.
- **Mastodon-first compatibility:** Prioritize interop with Mastodon over strict ActivityPub spec compliance. Test against Mastodon first; address edge cases with other implementations as they arise.
- **YAGNI:** Build only what's needed now. Features noted as future scope are excluded from the initial implementation.

## 2. Public Web Surface

### 2.1 Route Structure

| Route           | Purpose                                      | Auth     | Rendering                              |
|-----------------|----------------------------------------------|----------|----------------------------------------|
| `/`             | Home page — static welcome/branding page     | No       | Self-contained static HTML (inline CSS/JS) |
| `/{actor}`      | Public profile + AP actor endpoint           | No       | Self-contained static HTML (inline CSS/JS) for browsers; JSON-LD for AP consumers (content negotiation) |
| `/login`        | Login page                                   | No       | Self-contained static HTML (inline CSS/JS) |
| `/admin/*`      | Admin interface                              | Yes      | Static HTML shells + Web Components + JSON API |

### 2.2 Home Page

A simple static page served at `/` containing whatever welcome message or branding content the owner chooses. This is a plain HTML file — no dynamic data, no API calls, no JavaScript required.

### 2.3 Public Profile Page

The public profile page at `/{actor}` displays:

- Author display name, avatar, and short bio.
- Fediverse handle (e.g., `@cam@campegg.com`) with copy-to-clipboard.
- Links to elsewhere (other web presences, if desired).
- No feed, no post list, no pagination.

This is a self-contained static HTML file with all CSS and JS inline. Profile content (display name, bio rendered HTML, avatar, handle, links) is injected server-side via simple string interpolation from the settings table — not a template engine. The bio is stored as Markdown source and rendered to HTML (with the same typographic processing as notes — smart quotes, em/en dash, ellipsis) before injection. The page does not require JavaScript to display its core content.

A **"Follow me"** link is displayed on the page. Clicking it opens the visitor's Fediverse client pre-populated to follow this actor, using the actor's full AP URI as the target. This is implemented as a standard `<a>` link — no JavaScript required.

The `/{actor}` route is dual-purpose — it also serves as the ActivityPub actor endpoint. Content negotiation on the `Accept` header determines the response:

- **`application/activity+json` or `application/ld+json`:** Return the JSON-LD actor document (§4.1).
- **`text/html` (or browser default):** Return the public profile HTML page.

This is the canonical URI used in WebFinger and across federation.

### 2.4 ActivityPub Object Endpoints

Every published `Note` has a canonical URI on this server (e.g., `https://campegg.com/notes/{id}`). These endpoints:

- **Return JSON-LD** (`application/activity+json` or `application/ld+json`) when requested with an appropriate `Accept` header. This is required for federation — remote servers dereference these URIs to resolve threads, display reply chains, and verify objects.
- **Redirect to the home page** (`302 → /`) for browser requests (`Accept: text/html`). There is no HTML representation of individual notes.

**Future scope:** A query parameter on the redirect (e.g., `/?ref=notes/123`) could surface a message on the home page directing visitors to find the post on the fediverse. Not in initial implementation.

## 3. Notes

Short-form posts composed from the admin timeline and federated as ActivityPub `Note` objects.

### 3.1 Data Model

| Field          | Type              | Description                                                  |
|----------------|-------------------|--------------------------------------------------------------|
| `id`           | UUID (PK)         | Internal identifier, used in AP object URIs                  |
| `body`         | text              | Markdown source                                              |
| `body_html`    | text              | Rendered HTML (with typographic processing)                  |
| `ap_id`        | text (unique)     | Canonical ActivityPub object URI                             |
| `in_reply_to`  | text (nullable)   | AP URI of the post being replied to                          |
| `published_at` | datetime          | Publication timestamp                                        |
| `updated_at`   | datetime          | Last edit timestamp                                          |

UUIDs are used for all primary keys throughout the application. Sequential IDs leak content volume and are incompatible with ActivityPub object URLs where predictability is undesirable.

### 3.2 Edit Behaviour

When a note is edited (local or remote), the previous version is overwritten. No edit history is preserved.

**Rationale:** Edit history adds schema complexity (versioned content table, diffing or snapshot logic, UI for displaying history) disproportionate to its value in a single-user microblog. If this changes, it can be added later — `updated_at` timestamps are already tracked, providing a foundation.

### 3.3 Media Attachments

- **Upload:** Accept images (JPEG, PNG, WebP, GIF, HEIC) via the admin compose interface.
- **Storage:** Local filesystem under a configurable media directory (see §8.1).
- **Processing at upload time:** Pillow strips all metadata (EXIF, IPTC, XMP) and optimises the image. `pillow-heif` handles HEIC uploads, converting them to JPEG. No derivative sizes — store and serve a single optimised file per upload.
- **Federation:** Attached images are included as `attachment` objects on the outgoing `Note`.
- **Validation:** MIME type check and max file size enforcement on upload.

## 4. Federation & Protocol Layer

### 4.1 Actor & Discovery

- **Single actor** at a fixed `/{username}` endpoint. The username is set via environment variable (see §8.1). This is the same route as the public profile page (§2.3) — content negotiation determines whether to return the HTML profile or the JSON-LD actor document.
- **Actor document:** JSON-LD ActivityStreams object with `id`, `inbox`, `outbox`, `followers`, `following`, `preferredUsername`, `name`, `summary`, `icon`, `publicKey`. The `name`, `summary`, and `icon` fields are read from the database settings table (see §8.2). The `summary` field contains rendered HTML (the bio Markdown rendered with typographic processing), not the raw Markdown source.
- **WebFinger:** `/.well-known/webfinger?resource=acct:{user}@{domain}` returning the actor's `self` link (which points to `/{username}`).
- **NodeInfo:** `/.well-known/nodeinfo` (version 2.0) advertising software name, version, protocols, user count (1), post count.

### 4.2 HTTP Signatures

- **Signing:** All outgoing ActivityPub requests signed using HTTP Signatures (draft-cavage-http-signatures, RSA-SHA256). Keypair generated on first run and stored in the database.
- **Verification:** All incoming `POST` requests to the inbox must have their HTTP Signature verified against the sending actor's public key (fetched and cached from their actor document).
- **Verification failure fallback:** If signature verification fails against the cached public key, fetch the actor document fresh and retry verification once. This handles key rotation by remote actors without waiting for cache expiry.
- **Key rotation:** Provide a mechanism to regenerate the local keypair and update the actor document.

### 4.3 Outbox (Publishing)

- When a note is published, generate a `Create{Note}` activity and deliver it to all followers.
- **Object mapping:**
  - Rendered HTML → `content`
  - Raw Markdown → `source.content` (mediaType: `text/markdown`)
  - Tags → `tag` array (as `Hashtag` objects)
  - Media attachments → `attachment` array
- **Deletes:** On note delete, send `Delete` activity (with `Tombstone`) to all followers.
- **Updates:** On note edit, send `Update{Note}` to all followers.
- **Outbox endpoint:** `GET /{username}/outbox` returns an `OrderedCollection` of the actor's activities, paginated.

### 4.4 Inbox (Receiving)

- **Endpoint:** `POST /{username}/inbox`
- **Supported incoming activity types:**

  | Activity              | Behaviour                                                            |
  |-----------------------|----------------------------------------------------------------------|
  | `Follow`              | Auto-accept (send `Accept{Follow}` back), store follower             |
  | `Undo{Follow}`        | Remove follower                                                      |
  | `Create{Note}`        | Store in timeline if from a followed actor                           |
  | `Announce`            | Store in timeline, resolve original object                           |
  | `Like`                | Store notification, associate with local post if applicable          |
  | `Delete`              | Remove referenced object from local storage/timeline                 |
  | `Update`              | Overwrite locally cached version of the object                       |
  | `Undo{Like}`          | Remove corresponding record                                         |
  | `Undo{Announce}`      | Remove corresponding record                                         |
  | `Accept{Follow}`      | Mark outgoing follow request as accepted                             |
  | `Reject{Follow}`      | Mark outgoing follow request as rejected, remove                     |

- **Processing:** Incoming activities processed as async tasks (not inline in the HTTP handler). Return `202 Accepted` immediately.

### 4.5 Outgoing Interactions

| Action                 | Activity sent                   | Target                              |
|------------------------|---------------------------------|-------------------------------------|
| Follow a remote actor  | `Follow`                        | Remote actor's inbox                |
| Unfollow               | `Undo{Follow}`                  | Remote actor's inbox                |
| Like a remote post     | `Like`                          | Post author's inbox                 |
| Unlike                 | `Undo{Like}`                    | Post author's inbox                 |
| Reply to a remote post | `Create{Note}` with `inReplyTo` | Post author's inbox + followers     |
| Boost                  | `Announce`                      | Original author's inbox + followers |
| Undo boost             | `Undo{Announce}`                | Original author's inbox + followers |

### 4.6 Followers & Following

- **Followers collection:** `GET /{username}/followers` — `OrderedCollection`, paginated.
- **Following collection:** `GET /{username}/following` — `OrderedCollection`, paginated.
- **Storage:** Follower/following actor URIs, display names, avatar URLs, inbox URLs, shared inbox URLs, follow status (pending/accepted/rejected).

### 4.7 Object Storage & Caching

- **Remote actor cache:** Store fetched actor documents with a TTL (e.g., 24 hours). Re-fetch on cache miss or expiry. Additionally re-fetch on HTTP Signature verification failure (see §4.2). Cached fields: `uri`, `display_name`, `handle`, `bio` (from AP `summary`), `avatar_url` (from AP `icon.url`), `header_image_url` (from AP `image.url`), `inbox_url`, `shared_inbox_url`, `public_key`, `fetched_at`.
- **Remote object cache:** Store received notes for timeline display. Retain for a configurable period (e.g., 90 days), then prune.
- **Delivery tracking:** For each outbound activity, track delivery status per recipient (pending, delivered, failed, retries).

## 5. Admin Interface

### 5.1 Authentication

- **Single-user auth:** Username + password, stored as argon2 hash.
- **Session management:** Secure cookie (HttpOnly, Secure, SameSite=Strict).
- **CSRF protection:** Token on all state-changing requests.
- **Rate limiting:** On the login endpoint.
- **Login page:** Self-contained static HTML file at `static/pages/login.html` with all CSS and JS inline. A simple form that POSTs credentials to the auth endpoint. No JavaScript required for the form to function.

### 5.2 Timeline (Home)

Reverse-chronological feed of activities from followed accounts.

Each timeline item displays:

- Author avatar, display name, handle (`@user@domain`), relative timestamp.
- Post content (rendered HTML, sanitised), media attachments.
- Boost attribution (if boosted by a followed account).
- Interaction buttons: Like (toggle), Reply (opens inline compose), Boost (toggle).
- For own posts: Edit and Delete buttons.
- Visual indicators for items the user has already liked or boosted.

**Update mechanism:** The timeline loads by fetching the admin JSON API on initial page load and refreshes via polling at a configurable interval (e.g., every 30 seconds). A Web Component polls a lightweight JSON endpoint that returns new items since the most recent item ID or timestamp, and renders and prepends them to the top. Pagination for older items via "Load more" at the bottom.

### 5.3 Real-Time Notifications via SSE

A Server-Sent Events connection pushes notification-type events to the admin in real time:

- New followers.
- Likes on your posts.
- Boosts of your posts.
- Replies to your posts.

SSE is scoped to notifications only — the timeline uses polling (§5.2). This keeps the SSE handler decoupled from the full inbox processing pipeline. A `<notification-badge>` Web Component owns the SSE `EventSource` connection, establishes it on page load, and reconnects automatically on disconnect.

**Badge count:** On initialisation, `<notification-badge>` fetches the current unread count from a dedicated JSON API endpoint (`GET /admin/api/notifications/unread-count`), then increments the count by one for each incoming SSE event. This ensures the badge shows the correct count after a page reload or reconnect, not just new events received in the current session. The badge also listens on `document` for a `notifications-read` custom DOM event; when received, it resets the count to zero.

**Implementation:** The inbox processing pipeline emits notification events to an in-process `asyncio.Queue`. The SSE endpoint reads from this queue and streams events to the connected admin client. Since this is a single-user app, one queue and one consumer is sufficient.

### 5.4 Compose

A `<compose-box>` Web Component at the top of the timeline for creating new notes.

- Markdown input.
- Image attachment button (single or multiple images).
- Post button.
- Notes are federated immediately on publish.

### 5.5 Notifications View

Aggregated, persistent view of the same notification types pushed via SSE:

- New followers.
- Likes on your posts.
- Boosts of your posts.
- Replies to your posts.

Notifications are stored in the database and served via a paginated JSON API endpoint. A Web Component fetches and renders the notification list. The SSE events (§5.3) provide real-time alerts; this view provides the browsable history.

**Notification model fields:** `type` (follow/like/boost/reply), `actor_uri`, `actor_name`, `object_uri`, `content` (sanitised HTML reply text, for reply notifications), `read`.

**Mark-as-read on page load:** When the notifications view initialises, `<notification-list>` immediately calls a mark-all-read API endpoint (`POST /admin/api/notifications/mark-all-read`), which sets all `read = false` rows to `read = true`. After the call succeeds, the component dispatches a `notifications-read` custom DOM event on `document`, which the `<notification-badge>` component (present in the navigation on every admin page) listens for and uses to reset its count to zero. There is no per-item read toggle — viewing the notifications page is the read trigger.

**Follow notification items** include an inline Follow button (if not already following back) or an Unfollow text link (if already following back), actionable directly from the notification row.

**Reply notification items** display the reply content in a styled container within the notification row, along with like, reply, and boost action icons for the reply post.

### 5.6 Additional Views

Each additional view is a static HTML shell that loads Web Components to fetch data from admin JSON API endpoints.

- **Profile:** View and edit your own actor profile (name, bio, avatar, header image). The top of the profile edit form uses an `<actor-banner>` Web Component in `editable` mode — clicking either the banner or the avatar opens a file picker to replace that image independently. Edits update the database settings table (§8.2), which propagates to the public profile page and actor document. The profile tab also shows the user's own published notes below the edit form, with Edit and Delete controls on each.
- **Following / Followers:** Lists with Unfollow / Remove actions.
- **Likes:** Posts you have liked.
- **Search:** Remote actor lookup implemented as a **modal overlay** triggered from a search icon button in the admin navigation — not a separate page. The modal contains an input field for `@user@domain`, fetches the remote actor via WebFinger on submit, and displays a profile card with a Follow button on match or a "no result" message on failure.
- **Remote actor profile modal:** Clicking any actor name, handle, or avatar anywhere in the admin interface (timeline, notifications, followers, following, likes) opens a modal overlay. The modal (`<actor-profile-modal>`) renders a `<profile-card>` — a reusable component containing an `<actor-banner mode="static">` (banner + avatar in display-only mode), display name, handle, bio, and a `<follow-button>`. The same `<profile-card>` component, in `public` mode, is also used on the public-facing profile page.

### 5.7 Interaction Model

The admin interface uses **static HTML shells** served from `static/admin/` that load **Web Components** (Custom Elements). Components handle rendering, state, and interaction by fetching data from admin JSON API endpoints on Quart. There is no SPA framework, no client-side routing, no JS build step, and no server-side template engine.

Public pages (`/`, `/{actor}`, `/login`) are **not** part of the admin interface — they are self-contained static HTML files with all CSS and JS inline, served from `static/pages/`. They do not use Web Components or external JS/CSS files.

- **Navigation:** Full page loads between views. Each admin view (timeline, notifications, profile, following, followers, likes, search) is a distinct static HTML shell that loads the appropriate Web Components.
- **Timeline polling:** A Web Component polls the timeline JSON API endpoint via `fetch()` on a configurable interval and renders and prepends new items to the DOM.
- **Interaction toggles:** Like, boost, and follow/unfollow components use `fetch()` to call the admin JSON API and update their own state in place — no full page reload.
- **Inline reply:** A Web Component handles opening and managing an inline compose form via DOM manipulation.
- **Image upload:** Uses `fetch()` with `FormData`. The upload component shows a client-side preview before submission.
- **Notification badges:** A `<notification-badge>` Web Component owns the SSE `EventSource` connection, initialises its count from the unread count API endpoint, increments on each SSE event, and resets to zero when it receives a `notifications-read` DOM event (dispatched by `<notification-list>` after marking all read).
- **JS constraints:** Vanilla JavaScript only. No framework, no module bundler, no TypeScript. No build step. JS files served as static assets from `static/js/`. Progressive enhancement where feasible.

### 5.8 Web Component Reference

All Web Components live in `static/js/components/`. They are grouped here by build order: foundational leaf components first, then composites, then view/container components.

**Data sourcing pattern:** `<actor-identity>` and other display components are always told what to render — they never fetch data themselves. Data reaches them one of two ways:

- **Remote actor data** flows down from a parent container component (e.g., `<timeline-view>` fetches a list of posts from the JSON API and passes each author's name, handle, and avatar as attributes to the `<status-item>` and `<actor-identity>` components it renders).
- **Local user data** (nav bar avatar, profile edit heading) is injected server-side by Quart when it serves the admin HTML shell, as attributes on the relevant component (e.g., `<nav-bar user-name="..." user-handle="..." user-avatar="...">`). This avoids an extra API call on every page load and is consistent with how the public profile page already works.

The public profile page (`/{actor}`) does not use Web Components at all — it is a static HTML file with content injected at serve time via string interpolation.

---

#### Foundational leaf components (WP-13)

These have no Web Component dependencies and are built first.

---

**`<actor-identity>`** — avatar, display name, and handle grouped as a single unit.

| Attribute | Type | Description |
|---|---|---|
| `src` | string | Avatar image URL |
| `name` | string | Display name |
| `handle` | string | Full handle, e.g. `@user@domain` |
| `size` | `sm` \| `md` \| `lg` | Visual size variant |

Internal state: none. No API calls.

---

**`<follow-button>`** — Follow / Unfollow pill toggle.

| Attribute | Type | Description |
|---|---|---|
| `handle` | string | Actor handle used in the API call |
| `followed` | boolean | Initial follow state |

Internal state: current follow state (toggled on success), loading/pending flag.
API calls: `POST /admin/api/follow` / `POST /admin/api/unfollow`.
Fires: `follow` and `unfollow` custom DOM events (for parent components to react).

---

**`<actor-banner>`** — banner image with avatar anchored 24 px from the bottom-left.

| Attribute | Type | Description |
|---|---|---|
| `banner-src` | string | Banner image URL |
| `avatar-src` | string | Avatar image URL |
| `mode` | `static` \| `editable` | Display-only or click-to-replace |

Internal state (`editable` mode only): upload-in-progress flag and error state, tracked independently for banner and avatar.
API calls (`editable` mode): `POST /admin/api/upload` for each image replacement.

---

**`<notification-badge>`** — unread notification count pill (WP-16).

No HTML attributes. Manages its own data entirely.

Internal state: unread count integer, SSE `EventSource` connection.
API calls: `GET /admin/api/notifications/unread-count` on init.
Listens for: `notifications-read` DOM event on `document` (resets count to zero).
SSE: increments count by one for each incoming event; reconnects automatically on disconnect.

---

#### Composite components

Built after the leaf components they depend on.

---

**`<nav-bar>`** — primary admin navigation bar (WP-13).

| Attribute | Type | Description |
|---|---|---|
| `active` | string | Name of the currently active view (e.g. `timeline`, `notifications`) |
| `user-name` | string | Local user's display name (injected server-side) |
| `user-handle` | string | Local user's full handle (injected server-side) |
| `user-avatar` | string | Local user's avatar URL (injected server-side) |

Internal state: none beyond the attribute values.
Contains `<notification-badge>` as a child element; the badge manages its own state.
Passes `user-name`, `user-handle`, and `user-avatar` into an internal `<actor-identity>` — no API call required.

---

**`<status-item>`** — a single post in the timeline, likes, or profile view (WP-13).

| Attribute | Type | Description |
|---|---|---|
| `post-id` | string | Local or remote post identifier |
| `author-name` | string | Author display name |
| `author-handle` | string | Author handle |
| `author-avatar` | string | Author avatar URL |
| `published` | string | ISO 8601 timestamp |
| `body` | string | Rendered HTML content |
| `liked` | boolean | Whether the local user has liked this post |
| `like-count` | number | Total like count |
| `reposted` | boolean | Whether the local user has reposted this post |
| `repost-count` | number | Total repost count |
| `reply-count` | number | Total reply count |
| `own` | boolean | Whether this post belongs to the local user (shows Edit and Delete) |
| `media-url` | string | Optional attached image URL |

Internal state: like/repost toggle state, edit form open/closed, inline reply compose open/closed.
Fires: `like-toggled`, `repost-toggled`, `reply-submitted`, `edit-submitted`, `delete-confirmed`.
Composes: `<actor-identity>` for the author header.

---

**`<compose-box>`** — post compose form (WP-13; media wired in WP-14).

| Attribute | Type | Description |
|---|---|---|
| `in-reply-to` | string | Optional AP URI of the post being replied to |

Internal state: text content, attached media file list, character count, submit-in-progress flag.
Fires: `post-submitted` with the new post payload.

---

**`<person-row>`** — a single actor row for list views (WP-18).

| Attribute | Type | Description |
|---|---|---|
| `name` | string | Display name |
| `handle` | string | Full handle |
| `avatar` | string | Avatar image URL |
| `followed` | boolean | Current follow state |

Internal state: delegated entirely to child `<follow-button>`.
Composes: `<actor-identity>`, `<follow-button>`.

---

**`<notification-item>`** — a single notification row (WP-17).

| Attribute | Type | Description |
|---|---|---|
| `type` | `follow` \| `reply` | Controls which variant is rendered |
| `actor-name` | string | Notifying actor's display name |
| `actor-handle` | string | Notifying actor's handle |
| `actor-avatar` | string | Notifying actor's avatar URL |
| `actor-followed` | boolean | Whether the local user already follows this actor |
| `published` | string | ISO 8601 timestamp |
| `reply-body` | string | Rendered HTML of the reply (reply type only) |
| `reply-id` | string | Post identifier for reply action icons (reply type only) |

Internal state: delegated to child components.
Composes: `<actor-identity>` (both types), `<follow-button>` (follow type only).

---

**`<profile-card>`** — actor profile card used in the profile modal and public profile page (WP-18).

| Attribute | Type | Description |
|---|---|---|
| `name` | string | Display name |
| `handle` | string | Full handle |
| `avatar-src` | string | Avatar image URL |
| `banner-src` | string | Header image URL |
| `bio` | string | Rendered HTML biography |
| `followed` | boolean | Current follow state |
| `mode` | `modal` \| `public` | `modal`: interactive with `<follow-button>`; `public`: static display only, no JS interaction |

Internal state: delegated to child components.
Composes: `<actor-banner mode="static">`, `<actor-identity>`, `<follow-button>` (modal mode only).

---

**`<search-modal>`** — actor search overlay (WP-18).

No persistent HTML attributes. Opened and closed programmatically.

Internal state: open/closed flag, query string, search state (`idle` | `searching` | `results` | `empty`), result list (array of actor objects).
API calls: actor WebFinger/search endpoint on submit.
Keyboard: closes on Escape; input receives focus on open.
Composes: `<person-row>` for each result.

---

**`<actor-profile-modal>`** — remote actor profile overlay (WP-18).

No persistent HTML attributes.

Internal state: open/closed flag, actor URI being displayed, fetched actor data.
Listens for: `show-actor-profile` DOM event on `document` (payload: actor URI). Every other component that wants to surface an actor profile fires this event rather than opening the modal directly.
API calls: `GET /admin/api/actor?uri={uri}` on open to fetch current actor data.
Composes: `<profile-card mode="modal">`.

---

#### View / container components

These fetch and manage lists of data; they contain the item-level components above.

---

**`<timeline-view>`** (WP-13) — fetches and displays the admin timeline.

Internal state: ordered list of post items, latest item ID / timestamp for poll cursor, pagination cursor for "load more".
API calls: `GET /admin/api/timeline` on init and on each poll interval; `GET /admin/api/timeline?since={id}` for incremental updates; cursor-based `GET /admin/api/timeline?max_id={id}` for pagination.
Contains: `<status-item>` per post, `<compose-box>` at top.

---

**`<notification-list>`** (WP-17) — fetches and displays the notification history.

Internal state: ordered list of notification items, pagination cursor.
API calls: `GET /admin/api/notifications` on init; `POST /admin/api/notifications/mark-all-read` immediately after init.
Fires: `notifications-read` DOM event on `document` after mark-all-read succeeds.
Contains: `<notification-item>` per notification.

---

**`<following-list>`** (WP-18) — lists followed actors.

Internal state: ordered list of actors, pagination cursor.
API calls: `GET /admin/api/following`.
Contains: `<person-row>` per actor.

---

**`<followers-list>`** (WP-18) — lists followers.

Internal state: ordered list of actors, pagination cursor.
API calls: `GET /admin/api/followers`.
Contains: `<person-row>` per actor.

---

**`<likes-list>`** (WP-18) — lists liked posts.

Internal state: ordered list of post items, pagination cursor.
API calls: `GET /admin/api/likes`.
Contains: `<status-item>` per post.

---

## 6. Background Processing

### 6.1 In-Process Async Tasks

All background work runs inside the application process using `asyncio`. No separate worker, no external task queue, no Redis.

- Long-running work (delivery fan-out, remote actor fetching, cache pruning) dispatched via `asyncio.create_task()`.
- HTTP handlers return immediately.
- Concurrency bounded by `asyncio.Semaphore` (e.g., max 10 simultaneous outbound requests).

### 6.2 Crash Recovery

Every task that must survive a restart is persisted to an `ap_delivery_queue` table before dispatch. On startup, a recovery sweep re-enqueues incomplete tasks.

### 6.3 Delivery Pipeline

1. **Persist:** Write activity JSON and target inbox(es) to `ap_delivery_queue` with status `pending`.
2. **Fan-out:** Deduplicate by shared inbox where available. One async task per unique inbox URL, bounded by semaphore.
3. **Sign & send:** Construct activity JSON, compute HTTP Signature, POST via `httpx.AsyncClient`.
4. **Result:** On success (2xx), mark `delivered`. On failure, increment `attempts`, compute `next_retry_at` with exponential backoff (1m → 5m → 30m → 2h → 12h). Permanently fail after configurable max retries (default: 5).
5. **Dead instance detection:** If an inbox consistently fails over 7 days, flag follower as unreachable. Optionally prune after 30 days.

## 7. Database

### 7.1 Engine & Location

SQLite via SQLAlchemy 2.0 (async mode with aiosqlite backend). The database file lives at `tinker/db/tinker.db`.

### 7.2 SQLite Configuration

- **WAL mode:** Enable Write-Ahead Logging on first connection (`PRAGMA journal_mode=WAL`). This allows concurrent reads during writes and reduces contention from async task writes.
- **Busy timeout:** Set `PRAGMA busy_timeout=5000` as a safety net for concurrent write attempts.

### 7.3 Write Contention

SQLAlchemy's async session with the aiosqlite backend serialises writes through a single connection. Combined with WAL mode, this handles the concurrency constraints of SQLite without requiring a custom write queue. Under high fan-out (e.g., 200+ follower deliveries updating status), the busy timeout and WAL mode provide sufficient headroom.

### 7.4 Quart + SQLAlchemy Integration

- **Async engine and session factory:** Create an `AsyncEngine` (using the `sqlite+aiosqlite://` scheme) and an `async_sessionmaker` bound to it, configured to produce `AsyncSession` instances. Initialise both in the Quart app factory.
- **Request-scoped sessions:** Create a new `AsyncSession` at the start of each request via Quart's `before_request` hook and store it on the request context (e.g., `g.db_session`). Close it in `teardown_appcontext`. Each request gets its own session; connections are never leaked.
- **Background task sessions:** `asyncio.create_task()` tasks that need database access must create their own session from the `async_sessionmaker`. Do not share sessions across request/task boundaries.
- **SQLite PRAGMAs:** Apply WAL mode and busy timeout via a `@listens_for(engine.sync_engine, "connect")` event handler, so they are set on every new connection.

### 7.5 Alembic Configuration

Alembic requires a synchronous engine for `--autogenerate` migration generation. The Alembic `env.py` must be configured with a synchronous `Engine` created from the same database path (using the `sqlite:///` scheme instead of `sqlite+aiosqlite://`). Use this sync engine in both `run_migrations_offline()` and `run_migrations_online()`.

Migration files live in `tinker/alembic/`. Apply via `alembic upgrade head` as part of the deployment process.

### 7.6 IDs

All primary keys are UUIDs. Sequential IDs leak content volume and are incompatible with ActivityPub object URLs where predictability is undesirable.

## 8. Configuration

### 8.1 Environment Variables

Infrastructure-level configuration that differs between deployment environments. Loaded at startup, immutable at runtime. Read via a config module in `app/core/`.

| Variable              | Description                              | Example                          |
|-----------------------|------------------------------------------|----------------------------------|
| `TINKER_DOMAIN`       | Public domain for AP URIs and WebFinger  | `campegg.com`                    |
| `TINKER_DB_PATH`      | Path to SQLite database file             | `db/tinker.db`                   |
| `TINKER_MEDIA_PATH`   | Path to media storage directory          | `/var/lib/tinker/media/`         |
| `TINKER_SECRET_KEY`   | Secret key for session cookie signing    | (random string)                  |
| `TINKER_USERNAME`     | ActivityPub actor username               | `cam`                            |

A `.env` file is supported for local development (loaded via the config module). The `.env` file must be in `.gitignore` — never committed.

### 8.2 Database Settings

User-editable configuration stored in a `settings` table as key-value pairs. Readable at runtime, editable through the admin profile view (§5.6).

| Key              | Description                                        | Used by                              |
|------------------|----------------------------------------------------|--------------------------------------|
| `display_name`   | Author's display name                              | Public profile page, actor document  |
| `bio`            | Short biography / summary — stored as Markdown source, rendered to HTML (with typographic processing) for display on the public profile page and as the `summary` field in the actor document | Public profile page, actor document  |
| `avatar`         | Path to uploaded avatar image                      | Public profile page, actor document  |
| `links`          | JSON array of external URLs                        | Public profile page                  |

These values populate both the public profile page and the ActivityPub actor document. When edited through the admin, changes take effect immediately — no restart required.

### 8.3 Separation of Concerns

The distinction between §8.1 and §8.2 is: environment variables are infrastructure concerns that an operator sets once per deployment (domain, paths, secrets); database settings are identity and content concerns that the user edits through the admin interface (name, bio, links). If a value needs to change without restarting the application, it belongs in the database. If it should never be editable from within the running application, it belongs in an environment variable.

## 9. Security

- **Input sanitisation:** All received ActivityPub content sanitised with `nh3` before storage and display. Strict element allowlist.
- **Content Security Policy:** Strict CSP headers on the admin to prevent XSS from federated content.
- **SQL injection prevention:** All database access via SQLAlchemy ORM with parameterised queries. No raw SQL string construction.
- **Media upload validation:** MIME type check, max file size enforcement.
- **HTTPS:** Caddy handles TLS termination. Application enforces `Secure` cookie flags.
- **Avatar proxying:** Remote avatar URLs are not rendered directly in `<img>` tags. Fetch and cache avatars locally to prevent IP leakage and tracking pixels. Serve from a local path (e.g., `/media/avatars/`).

## 10. Technology Stack

| Layer              | Choice                                    | Rationale                                    |
|--------------------|-------------------------------------------|----------------------------------------------|
| Web framework      | Quart                                     | Async Flask-compatible, native SSE support   |
| ORM                | SQLAlchemy 2.0 (async)                    | Model definitions, query building, sessions  |
| Database           | SQLite (via aiosqlite)                    | Single-user, no need for a database server   |
| Migrations         | Alembic                                   | Schema versioning, up/down migrations        |
| HTTP client        | httpx                                     | Async, HTTP/2, well-maintained               |
| Crypto             | cryptography                              | HTTP Signature generation and verification   |
| HTML sanitisation  | nh3                                       | Rust-based, fast, allowlist sanitiser        |
| Markdown           | markdown-it-py (or similar)               | Render note body to HTML                     |
| Image processing   | Pillow + pillow-heif                      | Metadata stripping, optimisation, HEIC→JPEG  |
| Password hashing   | argon2-cffi                               | Not bcrypt                                   |
| Reverse proxy      | Caddy                                     | TLS, HTTPS, static file serving              |
| Background tasks   | asyncio (in-process)                      | No external dependencies                     |
| Admin UI           | Static HTML + Web Components + vanilla JS | No build step, no framework; JSON API-driven |
| Public pages       | Self-contained static HTML                | Inline CSS/JS, no external dependencies      |

## 11. Testing

### 11.1 Federation Integration Tests

End-to-end ActivityPub interaction tests using a mock remote server:

- **Mock AP server:** A minimal Quart app running in the test process that acts as a remote ActivityPub instance. Exposes an actor document, an inbox that records received activities, and a WebFinger endpoint.
- **Test scenarios:**
  - Follow → Accept round trip.
  - Publish a note → verify `Create{Note}` delivery with valid signature.
  - Receive Like → verify notification created.
  - Receive `Create{Note}` → verify timeline insertion.
  - Signature verification failure → verify re-fetch of actor document and retry.

## 12. CLAUDE.md

The project root must contain a `CLAUDE.md` file. This is a living operational reference for Claude Code — update it as the codebase evolves. See separate `CLAUDE.md` document for contents.

## 13. What's Explicitly Out of Scope

- Individual post pages (HTML). AP object endpoints return JSON-LD only.
- Article / long-form post types.
- Webmention (send or receive).
- Edit history for notes.
- Polls, custom emoji, `Move` activity support.
- Multi-user support.
- External task queues or message brokers.
- Full-text search of federated content.
- Private post archive view in the admin.
- Client-side routing or SPA frameworks in the admin.
- Server-side template engines (Jinja2, Mako, etc.).

**Future scope (acknowledged, not planned):**

- Home page redirect with post reference (`/?ref=notes/123`).
- Private admin archive of published notes.
- Edit history preservation.