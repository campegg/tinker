/**
 * <timeline-view> — fetches, renders, and keeps the admin timeline up to date.
 *
 * On connection:
 *   1. Fetches the first page of timeline items from /admin/api/timeline.
 *   2. Starts polling every 30 s for items newer than the most recent one.
 *   3. Renders a "Load more" button that fetches the next page on click.
 *
 * Listens for:
 *   post-submitted  — dispatched by <compose-box>; triggers a poll to prepend
 *                     the new post without a full reload.
 *   post-deleted    — dispatched by <status-item>; removes the item from
 *                     the internal list.
 *
 * No attributes. No shadow DOM.
 */
class TimelineView extends HTMLElement {
    /** ISO 8601 string of the newest item we have, for polling. */
    #newestPublished = null;
    /** ISO 8601 string of the oldest item we have, for "load more". */
    #cursor = null;
    #hasMore = false;
    #pollTimer = null;
    /** Ordered list of item data objects currently rendered. */
    #items = [];
    #loading = false;

    connectedCallback() {
        this.innerHTML = `<div class="timeline-view__empty">Loading…</div>`;
        this._load();
        this._startPolling();

        // Re-poll immediately when a new post is submitted via compose-box.
        document.addEventListener("post-submitted", () => this._poll());
    }

    disconnectedCallback() {
        this._stopPolling();
    }

    // -------------------------------------------------------------------------
    // Data fetching
    // -------------------------------------------------------------------------

    async _load() {
        this.#loading = true;
        try {
            const data = await _apiFetch("/admin/api/timeline");
            this.#items = data.data || [];
            this.#cursor = data.cursor || null;
            this.#hasMore = data.has_more || false;
            if (this.#items.length > 0) {
                this.#newestPublished = this.#items[0].published;
            }
            this._render();
        } catch {
            this.innerHTML = `<div class="timeline-view__empty">Failed to load timeline.</div>`;
        } finally {
            this.#loading = false;
        }
    }

    async _poll() {
        if (!this.#newestPublished) return;
        try {
            const url = `/admin/api/timeline?since=${encodeURIComponent(this.#newestPublished)}`;
            const data = await _apiFetch(url);
            const newItems = data.data || [];
            if (newItems.length === 0) return;

            // Prepend new items, deduplicating by id.
            const existingIds = new Set(this.#items.map(i => i.id));
            const fresh = newItems.filter(i => !existingIds.has(i.id));
            if (fresh.length === 0) return;

            this.#items = [...fresh, ...this.#items];
            this.#newestPublished = this.#items[0].published;
            this._renderList();
        } catch {
            // Silent — polling is best-effort
        }
    }

    async _loadMore() {
        if (!this.#cursor || this.#loading) return;
        this.#loading = true;
        try {
            const url = `/admin/api/timeline?before=${encodeURIComponent(this.#cursor)}`;
            const data = await _apiFetch(url);
            const older = data.data || [];
            if (older.length > 0) {
                const existingIds = new Set(this.#items.map(i => i.id));
                const fresh = older.filter(i => !existingIds.has(i.id));
                this.#items = [...this.#items, ...fresh];
                this.#cursor = data.cursor || null;
                this.#hasMore = data.has_more || false;
            }
            this._render();
        } catch {
            // Silent
        } finally {
            this.#loading = false;
        }
    }

    // -------------------------------------------------------------------------
    // Polling
    // -------------------------------------------------------------------------

    _startPolling() {
        this._stopPolling();
        this.#pollTimer = setInterval(() => this._poll(), 30_000);
    }

    _stopPolling() {
        if (this.#pollTimer !== null) {
            clearInterval(this.#pollTimer);
            this.#pollTimer = null;
        }
    }

    // -------------------------------------------------------------------------
    // Rendering
    // -------------------------------------------------------------------------

    _render() {
        if (this.#items.length === 0) {
            this.innerHTML = `<div class="timeline-view__empty">Nothing here yet.</div>`;
            return;
        }

        const listId = "timeline-list";
        let container = this.querySelector(`#${listId}`);
        if (!container) {
            this.innerHTML = `<div id="${listId}"></div>`;
            container = this.querySelector(`#${listId}`);
        }

        this._renderList();
        this._renderLoadMore();
    }

    _renderList() {
        const listEl = this.querySelector("#timeline-list");
        if (!listEl) return;

        // Reconcile: remove items that are no longer in #items.
        const currentIds = new Set(this.#items.map(i => i.id));
        listEl.querySelectorAll("status-item").forEach(el => {
            if (!currentIds.has(el.getAttribute("data-timeline-id"))) {
                el.remove();
            }
        });

        // Prepend new items that don't have DOM nodes yet.
        const existingEls = new Map(
            Array.from(listEl.querySelectorAll("status-item"))
                .map(el => [el.getAttribute("data-timeline-id"), el])
        );

        // Build in correct order (newest first) by reinserting all.
        // For efficiency on a single-user server, full re-render is acceptable.
        listEl.innerHTML = this.#items.map(item => _itemHtml(item)).join("");

        // Bind delete events so items remove themselves from #items too.
        listEl.querySelectorAll("status-item").forEach(el => {
            el.addEventListener("post-deleted", (e) => {
                const { internalId } = e.detail;
                this.#items = this.#items.filter(i => i.internal_id !== internalId);
            });
        });
    }

    _renderLoadMore() {
        let btn = this.querySelector(".timeline-view__load-more");
        if (this.#hasMore) {
            if (!btn) {
                btn = document.createElement("div");
                btn.className = "timeline-view__load-more";
                btn.innerHTML = `<button class="compose-box__btn js-load-more">Load more…</button>`;
                this.append(btn);
            }
            btn.querySelector(".js-load-more")
                ?.addEventListener("click", () => this._loadMore(), { once: true });
        } else {
            btn?.remove();
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _esc(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

async function _apiFetch(url) {
    const resp = await fetch(url, { credentials: "same-origin" });
    if (!resp.ok) throw new Error(`API error ${resp.status}`);
    return resp.json();
}

function _itemHtml(item) {
    return `<status-item
        data-timeline-id="${_esc(item.id)}"
        post-id="${_esc(item.post_id)}"
        ${item.internal_id ? `internal-id="${_esc(item.internal_id)}"` : ""}
        author-name="${_esc(item.author_name)}"
        author-handle="${_esc(item.author_handle)}"
        author-avatar="${_esc(item.author_avatar)}"
        published="${_esc(item.published)}"
        body="${_esc(item.body_html)}"
        ${item.media_url ? `media-url="${_esc(item.media_url)}"` : ""}
        ${item.liked ? `liked="true"` : ""}
        ${item.reposted ? `reposted="true"` : ""}
        ${item.own ? `own="true"` : ""}
    ></status-item>`;
}

customElements.define("timeline-view", TimelineView);
