/**
 * <likes-list> — paginated list of posts the local user has liked.
 *
 * On connection:
 *   1. Fetches GET /admin/api/likes (first page).
 *   2. Renders each result as a <status-item> (without `own` attribute).
 *   3. Shows a "Load more" button if there are additional pages.
 *
 * No attributes. No shadow DOM.
 */
class LikesList extends HTMLElement {
    #cursor = null;
    #hasMore = false;
    #loading = false;

    connectedCallback() {
        this.innerHTML = `<div class="likes-list__empty">Loading…</div>`;
        this._load();
    }

    async _load() {
        this.#loading = true;
        try {
            const data = await _apiFetch("/admin/api/likes");
            this.#cursor = data.cursor || null;
            this.#hasMore = data.has_more || false;
            this._renderItems(data.data || [], false);
            this._renderLoadMore();
        } catch {
            this.innerHTML = `<div class="likes-list__empty">Failed to load liked posts.</div>`;
        } finally {
            this.#loading = false;
        }
    }

    async _loadMore() {
        if (!this.#cursor || this.#loading) return;
        this.#loading = true;
        try {
            const url = `/admin/api/likes?before=${encodeURIComponent(this.#cursor)}`;
            const data = await _apiFetch(url);
            this.#cursor = data.cursor || null;
            this.#hasMore = data.has_more || false;
            this._renderItems(data.data || [], true);
            this._renderLoadMore();
        } catch {
            // Silent — best effort
        } finally {
            this.#loading = false;
        }
    }

    _renderItems(items, append) {
        let list = this.querySelector("#likes-list");
        if (!list) {
            this.innerHTML = `<div id="likes-list"></div>`;
            list = this.querySelector("#likes-list");
        }

        if (items.length === 0 && !append) {
            this.innerHTML = `<div class="likes-list__empty">No liked posts yet.</div>`;
            return;
        }

        const html = items.map((item) => _statusHtml(item)).join("");
        if (append) {
            list.insertAdjacentHTML("beforeend", html);
        } else {
            list.innerHTML = html;
        }
    }

    _renderLoadMore() {
        let btn = this.querySelector(".likes-list__load-more");
        if (this.#hasMore) {
            if (!btn) {
                btn = document.createElement("div");
                btn.className = "likes-list__load-more";
                btn.innerHTML = `<button class="likes-list__load-more-btn js-load-more">Load more…</button>`;
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

function _statusHtml(item) {
    return `<status-item
        post-id="${_esc(item.post_id)}"
        author-name="${_esc(item.author_name || "")}"
        author-handle="${_esc(item.author_handle || "")}"
        author-avatar="${_esc(item.author_avatar || "")}"
        published="${_esc(item.published || "")}"
        body="${_esc(item.body_html || "")}"
        liked="true"
        ${item.media_url ? `media-url="${_esc(item.media_url)}"` : ""}
    ></status-item>`;
}

customElements.define("likes-list", LikesList);
