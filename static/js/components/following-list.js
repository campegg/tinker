/**
 * <following-list> — paginated list of actors the local user follows.
 *
 * On connection:
 *   1. Fetches GET /admin/api/following (first page).
 *   2. Renders each result as a <person-row>.
 *   3. Shows a "Load more" button if there are additional pages.
 *
 * No attributes. No shadow DOM.
 */
class FollowingList extends HTMLElement {
    #cursor = null;
    #hasMore = false;
    #loading = false;

    connectedCallback() {
        this.innerHTML = `<div class="following-list__empty">Loading…</div>`;
        this._load();
    }

    async _load() {
        this.#loading = true;
        try {
            const data = await _apiFetch("/admin/api/following");
            this.#cursor = data.cursor || null;
            this.#hasMore = data.has_more || false;
            this._renderItems(data.data || [], false);
            this._renderLoadMore();
        } catch {
            this.innerHTML = `<div class="following-list__empty">Failed to load following.</div>`;
        } finally {
            this.#loading = false;
        }
    }

    async _loadMore() {
        if (!this.#cursor || this.#loading) return;
        this.#loading = true;
        try {
            const url = `/admin/api/following?before=${encodeURIComponent(this.#cursor)}`;
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
        let list = this.querySelector("#following-list");
        if (!list) {
            this.innerHTML = `<div id="following-list"></div>`;
            list = this.querySelector("#following-list");
        }

        if (items.length === 0 && !append) {
            this.innerHTML = `<div class="following-list__empty">Not following anyone yet.</div>`;
            return;
        }

        const html = items.map((item) => _rowHtml(item)).join("");
        if (append) {
            list.insertAdjacentHTML("beforeend", html);
        } else {
            list.innerHTML = html;
        }
    }

    _renderLoadMore() {
        let btn = this.querySelector(".following-list__load-more");
        if (this.#hasMore) {
            if (!btn) {
                btn = document.createElement("div");
                btn.className = "following-list__load-more";
                btn.innerHTML = `<button class="following-list__load-more-btn js-load-more">Load more…</button>`;
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

function _rowHtml(item) {
    return `<person-row
        actor-uri="${_esc(item.actor_uri)}"
        name="${_esc(item.display_name || item.actor_uri)}"
        handle="${_esc(item.handle || "")}"
        avatar="${_esc(item.avatar_url || "")}"
        following
    ></person-row>`;
}

customElements.define("following-list", FollowingList);
