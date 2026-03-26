/**
 * <followers-list> — paginated list of actors following the local user.
 *
 * On connection:
 *   1. Fetches GET /admin/api/followers (first page).
 *   2. Renders each result as a <person-row> plus a "Remove" button.
 *   3. Remove button sends DELETE /admin/api/followers and removes the row.
 *   4. Shows a "Load more" button if there are additional pages.
 *
 * No attributes. No shadow DOM.
 */
class FollowersList extends HTMLElement {
    #cursor = null;
    #hasMore = false;
    #loading = false;

    connectedCallback() {
        this.innerHTML = `<div class="followers-list__empty">Loading…</div>`;
        this._load();
    }

    async _load() {
        this.#loading = true;
        try {
            const data = await _apiFetch("/admin/api/followers");
            this.#cursor = data.cursor || null;
            this.#hasMore = data.has_more || false;
            this._renderItems(data.data || [], false);
            this._renderLoadMore();
        } catch {
            this.innerHTML = `<div class="followers-list__empty">Failed to load followers.</div>`;
        } finally {
            this.#loading = false;
        }
    }

    async _loadMore() {
        if (!this.#cursor || this.#loading) return;
        this.#loading = true;
        try {
            const url = `/admin/api/followers?before=${encodeURIComponent(this.#cursor)}`;
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
        let list = this.querySelector("#followers-list");
        if (!list) {
            this.innerHTML = `<div id="followers-list"></div>`;
            list = this.querySelector("#followers-list");
        }

        if (items.length === 0 && !append) {
            this.innerHTML = `<div class="followers-list__empty">No followers yet.</div>`;
            return;
        }

        const frag = document.createDocumentFragment();
        for (const item of items) {
            const wrap = document.createElement("div");
            wrap.className = "followers-list__row";
            wrap.dataset.actorUri = item.actor_uri;
            wrap.innerHTML = `
                <person-row
                    actor-uri="${_esc(item.actor_uri)}"
                    name="${_esc(item.display_name || item.actor_uri)}"
                    handle="${_esc(item.handle || "")}"
                    avatar="${_esc(item.avatar_url || "")}"
                ></person-row>
                <button
                    class="followers-list__remove-btn"
                    aria-label="Remove follower"
                >Remove</button>`;
            wrap.querySelector(".followers-list__remove-btn")
                ?.addEventListener("click", () => this._removeFollower(item.actor_uri, wrap));
            frag.append(wrap);
        }

        if (append) {
            list.append(frag);
        } else {
            list.replaceChildren(frag);
        }
    }

    async _removeFollower(actorUri, rowEl) {
        try {
            const resp = await fetch("/admin/api/followers", {
                method: "DELETE",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": window.__TINKER__?.csrf || "",
                },
                body: JSON.stringify({ actor_uri: actorUri }),
            });
            if (resp.ok) {
                rowEl.remove();
            }
        } catch {
            // Silent — let the user retry
        }
    }

    _renderLoadMore() {
        let btn = this.querySelector(".followers-list__load-more");
        if (this.#hasMore) {
            if (!btn) {
                btn = document.createElement("div");
                btn.className = "followers-list__load-more";
                btn.innerHTML = `<button class="followers-list__load-more-btn js-load-more">Load more…</button>`;
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

customElements.define("followers-list", FollowersList);
