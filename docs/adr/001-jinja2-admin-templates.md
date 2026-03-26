# ADR-001: Jinja2 Templates for Admin Shell Pages

## Date
2025-03-25

## Status
Accepted

## Context

The admin interface is built as a set of HTML shell pages that each load
a collection of Web Components. Each shell is a complete HTML document
containing a shared `<head>` block, a `<nav-bar>` element, a `<main>`
region with the page-specific component, and a set of `<script type="module">`
tags.

As the number of admin views grew (timeline, notifications, profile,
following, followers, likes — six pages as of WP-17, with more planned for
WP-18), the boilerplate duplicated across each file became a maintenance
problem. Every page repeated identical `<head>` metadata, the same
`window.__TINKER__` bootstrap script, and a core set of foundation scripts
(`actor-identity.js`, `nav-bar.js`, `notification-badge.js`). A change to
any of these — adding a `<meta>` tag, renaming a JS file, tweaking the
CSRF bootstrap format — required editing every shell individually.

The original approach used a bespoke `_load_shell()` / `_inject()` mechanism
in `app/admin/routes.py` that read each HTML file from `static/admin/`,
performed `str.replace()` substitutions for `{{csrf_token}}`, `{{user_name}}`,
`{{user_handle}}`, and `{{user_avatar}}` markers, and returned the result as
a response. This was simple and required no new dependencies, but it had two
compounding problems:

1. **Duplication.** Each shell file was a full HTML document. Shared structure
   could not be factored out — it had to be copied.

2. **No HTML escaping.** `str.replace()` performs no escaping. A display name
   containing `<script>alert(1)</script>` would be injected verbatim into the
   `user-name` attribute of `<nav-bar>`, producing a stored XSS vector in the
   admin interface. The admin is single-user, but defence-in-depth still
   applies — a crafted display name received from a remote actor and saved via
   the profile edit form could reach this path.

Jinja2 is already present in the virtualenv as a direct dependency of Quart
(Quart inherits Flask's Jinja2 integration). Using it does not add a new
package to `pyproject.toml` or `uv.lock`.

## Decision

Use Jinja2 template inheritance for admin shell pages.

- A shared base template at `templates/admin/base.html` owns all common
  structure: the `<!doctype>`, `<head>`, `<body>` wrapper, the
  `window.__TINKER__` bootstrap script, the `<nav-bar>` element, the
  `<main>` wrapper, and the three foundation `<script>` tags that appear
  on every page.
- Each admin view has a minimal child template (e.g.
  `templates/admin/timeline.html`) that extends the base and overrides
  three blocks: `{% block title %}`, `{% block main %}`, and
  `{% block scripts %}`.
- `app/admin/routes.py` replaces the `_load_shell()` / `_inject()` /
  `_template_cache` machinery with `render_template()` calls and a single
  `_shell_context(nav_active)` helper that returns the template variable dict.
- The `Quart()` constructor in `app/__init__.py` is given an explicit
  `template_folder="../templates"` argument so that Jinja2's loader resolves
  templates relative to the project root, consistent with the existing
  `static_folder="../static"` argument.

The public-facing pages (`/`, `/{actor}`, `/login`) are **not** changed.
They remain in `static/pages/` and are served either as fully static files
(home, login) or with targeted string interpolation at the route level
(public profile). Applying Jinja2 uniformly across all pages would offer
little benefit for pages that are either fully static or require only one or
two injections, and it would add indirection where none is needed.

## Alternatives Considered

**Expand the bespoke `{{marker}}` substitution.**
The existing `_inject()` mechanism could be extended to replace a `{{head}}`
and `{{scripts_common}}` marker with pre-built Python strings, reducing file
duplication without introducing Jinja2. This was rejected because it replaces
one form of duplication (copy-pasted HTML) with another (Python string
constants that must be kept in sync with every shell file), and still provides
no HTML escaping.

**Assemble pages entirely in Python (no shell files).**
Each route could build the full HTML response in a Python function, with the
page-specific content fragment loaded from a small file. This would centralise
everything in one place and avoid any template file format. It was rejected
because embedding HTML structure in Python f-strings is harder to read, harder
to lint, and offers no advantage over Jinja2 given that Jinja2 is already
available.

**Use Jinja2 for all pages including public ones.**
The public profile page (`/{actor}`) injects display name, bio, avatar, handle,
and links. Porting it to Jinja2 would provide consistent escaping there too.
This was scoped out of this change because the public profile has specific
performance characteristics (the template cache in `public/routes.py` avoids
DB reads on repeat requests) and its injection surface is already reviewed and
understood. The benefit did not justify the scope increase.

## Consequences

**Easier:**
- Adding a new admin view requires one new child template (four to ten lines)
  and one `render_template()` call in `routes.py`. No other files change.
- Changes to shared structure (head metadata, nav markup, foundation scripts,
  CSRF bootstrap format) are made once in `base.html` and take effect across
  all admin pages immediately.
- Injected user values (display name, handle, avatar URL, CSRF token) are
  HTML-escaped by Jinja2's auto-escaping, which is on by default for `.html`
  templates. The XSS vector present in the `str.replace()` approach is closed.
- Jinja2's built-in template loader handles caching (keyed on template path
  and mtime), replacing the hand-rolled `_template_cache` dict in `routes.py`.
  The `QUART_DEBUG=true` reload behaviour is preserved automatically.

**Harder / things to watch:**
- Template files must not be placed in `static/` — they belong in `templates/`
  and are not served as static assets.
- Jinja2 auto-escaping means that values known to contain safe HTML (e.g. a
  future rendered bio injected into the admin profile shell) must be explicitly
  marked with `| safe`. This is intentional friction — it forces a conscious
  decision at the point of injection and should only be done for values that
  have been sanitised upstream (e.g. via `nh3`).
- The public profile's string-interpolation approach now diverges from the
  admin approach. This asymmetry is documented in CLAUDE.md and is intentional
  — the two contexts have different requirements and different audiences.