/**
 * <notification-list> — fetches and renders the admin notification history.
 *
 * On connection:
 *   1. Fetches the first page of notifications from /admin/api/notifications.
 *   2. Renders each as a <notification-item> element.
 *   3. POSTs to /admin/api/notifications/mark-all-read and, on success,
 *      dispatches a `notifications-read` event on `document` so that
 *      <notification-badge> resets its count.
 *   4. If there are more pages, renders a "Load more" button.
 *
 * No attributes. No shadow DOM.
 */
class NotificationList extends HTMLElement {
    #cursor = null;
    #hasMore = false;
    #loading = false;

    connectedCallback() {
        this.innerHTML = `<div class="notification-list__empty">Loading…</div>`;
        this._load();
    }

    async _load() {
        this.#loading = true;
        try {
            const data = await _apiFetch("/admin/api/notifications");
            this.#cursor = data.cursor || null;
            this.#hasMore = data.has_more || false;
            this._renderItems(data.data || [], false);
            this._renderLoadMore();
            this._markAllRead();
        } catch {
            this.innerHTML = `<div class="notification-list__empty">Failed to load notifications.</div>`;
        } finally {
            this.#loading = false;
        }
    }

    async _loadMore() {
        if (!this.#cursor || this.#loading) return;
        this.#loading = true;
        try {
            const url = `/admin/api/notifications?before=${encodeURIComponent(this.#cursor)}`;
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
        let list = this.querySelector("#notification-list");
        if (!list) {
            this.innerHTML = `<div id="notification-list"></div>`;
            list = this.querySelector("#notification-list");
        }

        if (items.length === 0 && !append) {
            this.innerHTML = `<div class="notification-list__empty">No notifications yet.</div>`;
            return;
        }

        const html = items.map(item => _itemHtml(item)).join("");
        if (append) {
            list.insertAdjacentHTML("beforeend", html);
        } else {
            list.innerHTML = html;
        }
    }

    _renderLoadMore() {
        let btn = this.querySelector(".notification-list__load-more");
        if (this.#hasMore) {
            if (!btn) {
                btn = document.createElement("div");
                btn.className = "notification-list__load-more";
                btn.innerHTML = `<button class="compose-box__btn js-load-more">Load more…</button>`;
                this.append(btn);
            }
            btn.querySelector(".js-load-more")
                ?.addEventListener("click", () => this._loadMore(), { once: true });
        } else {
            btn?.remove();
        }
    }

    async _markAllRead() {
        try {
            const resp = await fetch("/admin/api/notifications/mark-all-read", {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": window.__TINKER__?.csrf || "",
                },
            });
            if (resp.ok) {
                document.dispatchEvent(new CustomEvent("notifications-read", { bubbles: true }));
            }
        } catch {
            // Non-critical — badge will just keep its previous count
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
    const isFollowing = item.is_following ? ` is-following` : "";
    return `<notification-item
        type="${_esc(item.type)}"
        actor-uri="${_esc(item.actor_uri)}"
        actor-name="${_esc(item.actor_name)}"
        actor-handle="${_esc(item.actor_handle)}"
        actor-avatar="${_esc(item.actor_avatar)}"
        object-uri="${_esc(item.object_uri)}"
        content="${_esc(item.content)}"
        published="${_esc(item.created_at)}"
        ${isFollowing}
    ></notification-item>`;
}

customElements.define("notification-list", NotificationList);
