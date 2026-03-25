/**
 * <status-item> — a single post in the timeline, likes view, or profile view.
 *
 * Attributes:
 *   post-id        — AP URI of the post (used as key and for API calls)
 *   internal-id    — local UUID for own notes (required for edit/delete)
 *   author-name    — author display name
 *   author-handle  — author full handle, e.g. @user@domain
 *   author-avatar  — author avatar URL
 *   published      — ISO 8601 timestamp string
 *   body           — sanitised HTML content of the post
 *   liked          — "true" if the local user has liked this post
 *   reposted       — "true" if the local user has reposted this post
 *   own            — "true" for own posts (shows Edit and Delete controls)
 *   media-url      — optional attached image URL
 *
 * Fires:
 *   post-deleted  — when a delete is confirmed; detail: { internalId }
 *   post-updated  — when an edit is submitted; detail: { internalId, body }
 */
class StatusItem extends HTMLElement {
    static observedAttributes = [
        "post-id", "internal-id", "author-name", "author-handle",
        "author-avatar", "published", "body", "liked", "reposted",
        "own", "media-url",
    ];

    #liked = false;
    #reposted = false;
    #editOpen = false;
    #replyOpen = false;

    connectedCallback() {
        this.#liked = this.getAttribute("liked") === "true";
        this.#reposted = this.getAttribute("reposted") === "true";
        this._render();
    }

    attributeChangedCallback(name) {
        if (!this.isConnected) return;
        if (name === "liked") this.#liked = this.getAttribute("liked") === "true";
        if (name === "reposted") this.#reposted = this.getAttribute("reposted") === "true";
        this._render();
    }

    _csrf() {
        return window.__TINKER__?.csrf || "";
    }

    _render() {
        const name = this.getAttribute("author-name") || "";
        const handle = this.getAttribute("author-handle") || "";
        const avatar = this.getAttribute("author-avatar") || "";
        const published = this.getAttribute("published") || "";
        const body = this.getAttribute("body") || "";
        const mediaUrl = this.getAttribute("media-url") || "";
        const own = this.getAttribute("own") === "true";
        const postId = this.getAttribute("post-id") || "";
        const internalId = this.getAttribute("internal-id") || "";

        const relTime = _relativeTime(published);
        const avatarEl = avatar
            ? `<img class="status-item__avatar" src="${_esc(avatar)}" alt="${_esc(name)}" loading="lazy">`
            : `<span class="status-item__avatar" aria-hidden="true"></span>`;

        const mediaEl = mediaUrl
            ? `<div class="status-item__media"><img src="${_esc(mediaUrl)}" alt="" loading="lazy"></div>`
            : "";

        const likedClass = this.#liked ? ' data-active="true" class="status-item__action-btn status-item__like"' : 'class="status-item__action-btn status-item__like"';
        const repostedAttr = this.#reposted ? ' data-active="true"' : "";
        const replyActive = this.#replyOpen ? ' data-active="true"' : "";

        const ownActions = own
            ? `<div class="status-item__actions-end">
                   <button class="status-item__action-btn js-edit" aria-label="Edit">${_iconEdit()}</button>
                   <button class="status-item__action-btn js-delete" aria-label="Delete">${_iconTrash()}</button>
               </div>`
            : "";

        const editForm = own && this.#editOpen
            ? `<form class="compose-box js-edit-form" style="margin-block-start: var(--margin-xs);">
                   <textarea class="compose-box__field js-edit-body" style="block-size: calc(var(--margin-xxl) * 2);" aria-label="Edit post">${_esc(body.replace(/<[^>]+>/g, ""))}</textarea>
                   <div class="compose-box__toolbar">
                       <div class="compose-box__toolbar-start"></div>
                       <div style="display:flex;gap:var(--margin-xs)">
                           <button type="button" class="compose-box__btn js-edit-cancel">Cancel</button>
                           <button type="submit" class="compose-box__btn js-edit-save">Save</button>
                       </div>
                   </div>
               </form>`
            : "";

        const replyForm = this.#replyOpen
            ? `<form class="compose-box js-reply-form" style="margin-block-start: var(--margin-xs);">
                   <textarea class="compose-box__field js-reply-body" style="block-size: calc(var(--margin-xxl) * 2);" placeholder="Write a reply…" aria-label="Reply"></textarea>
                   <div class="compose-box__toolbar">
                       <div class="compose-box__toolbar-start"></div>
                       <div style="display:flex;gap:var(--margin-xs)">
                           <button type="button" class="compose-box__btn js-reply-cancel">Cancel</button>
                           <button type="submit" class="compose-box__btn js-reply-post">Reply</button>
                       </div>
                   </div>
               </form>`
            : "";

        this.className = "status-item";
        this.innerHTML = `
            ${avatarEl}
            <div class="status-item__body-col">
                <div class="status-item__meta">
                    <span class="status-item__name js-actor-link" data-uri="${_esc(postId)}">${_esc(name)}</span>
                    <span class="status-item__handle-date">
                        <span class="status-item__handle js-actor-link" data-uri="${_esc(postId)}">${_esc(handle)}</span>
                        <span aria-hidden="true"> &bull; </span>
                        <time datetime="${_esc(published)}" title="${_esc(published)}">${_esc(relTime)}</time>
                    </span>
                </div>
                <div class="status-item__content">${body}</div>
                ${mediaEl}
                <div class="status-item__actions">
                    <button ${likedClass} data-post-id="${_esc(postId)}" aria-label="Like" aria-pressed="${this.#liked}">${_iconHeart()}
                    </button>
                    <button class="status-item__action-btn status-item__reply js-reply"${replyActive} aria-label="Reply" aria-pressed="${this.#replyOpen}">${_iconReply()}</button>
                    <button class="status-item__action-btn status-item__repost js-repost"${repostedAttr} data-post-id="${_esc(postId)}" aria-label="Repost" aria-pressed="${this.#reposted}">${_iconRepost()}
                    </button>
                    ${ownActions}
                </div>
                ${editForm}
                ${replyForm}
            </div>`;

        this._bindEvents(internalId, postId, body);
    }

    _bindEvents(internalId, postId, currentBody) {
        // Actor profile modal trigger
        this.querySelectorAll(".js-actor-link").forEach(el => {
            el.addEventListener("click", () => {
                document.dispatchEvent(new CustomEvent("show-actor-profile", {
                    bubbles: true,
                    detail: { uri: postId },
                }));
            });
        });

        // Avatar click
        const avatarEl = this.querySelector(".status-item__avatar");
        avatarEl?.addEventListener("click", () => {
            document.dispatchEvent(new CustomEvent("show-actor-profile", {
                bubbles: true,
                detail: { uri: postId },
            }));
        });

        // Like toggle
        this.querySelector(".status-item__like")
            ?.addEventListener("click", () => this._toggleLike(postId));

        // Repost toggle
        this.querySelector(".js-repost")
            ?.addEventListener("click", () => this._toggleRepost(postId));

        // Reply
        this.querySelector(".js-reply")
            ?.addEventListener("click", () => {
                this.#replyOpen = !this.#replyOpen;
                this._render();
            });

        // Edit
        this.querySelector(".js-edit")
            ?.addEventListener("click", () => {
                this.#editOpen = !this.#editOpen;
                this._render();
            });
        this.querySelector(".js-edit-cancel")
            ?.addEventListener("click", () => {
                this.#editOpen = false;
                this._render();
            });
        this.querySelector(".js-edit-form")
            ?.addEventListener("submit", (e) => {
                e.preventDefault();
                const newBody = this.querySelector(".js-edit-body")?.value.trim();
                if (!newBody) return;
                this._submitEdit(internalId, newBody);
            });

        // Reply form
        this.querySelector(".js-reply-cancel")
            ?.addEventListener("click", () => {
                this.#replyOpen = false;
                this._render();
            });
        this.querySelector(".js-reply-form")
            ?.addEventListener("submit", (e) => {
                e.preventDefault();
                const replyBody = this.querySelector(".js-reply-body")?.value.trim();
                if (!replyBody) return;
                this._submitReply(postId, replyBody);
            });

        // Delete
        this.querySelector(".js-delete")
            ?.addEventListener("click", () => this._confirmDelete(internalId));
    }

    async _toggleLike(postId) {
        const next = !this.#liked;
        const endpoint = next ? "/admin/api/likes" : "/admin/api/unlikes";
        try {
            const resp = await fetch(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRF-Token": this._csrf() },
                body: JSON.stringify({ post_id: postId }),
            });
            if (!resp.ok) return;
            this.#liked = next;
            this._render();
        } catch {
            // Network error — ignore silently
        }
    }

    async _toggleRepost(postId) {
        const next = !this.#reposted;
        const endpoint = next ? "/admin/api/boosts" : "/admin/api/unboosts";
        try {
            const resp = await fetch(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRF-Token": this._csrf() },
                body: JSON.stringify({ post_id: postId }),
            });
            if (!resp.ok) return;
            this.#reposted = next;
            this._render();
        } catch {
            // Network error — ignore silently
        }
    }

    async _submitEdit(internalId, body) {
        if (!internalId) return;
        try {
            const resp = await fetch(`/admin/api/notes/${internalId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json", "X-CSRF-Token": this._csrf() },
                body: JSON.stringify({ body }),
            });
            if (!resp.ok) return;
            this.#editOpen = false;
            this.setAttribute("body", `<p>${_esc(body)}</p>`);
            this.dispatchEvent(new CustomEvent("post-updated", {
                bubbles: true,
                detail: { internalId, body },
            }));
        } catch {
            // Network error — ignore silently
        }
    }

    async _submitReply(postId, body) {
        try {
            const resp = await fetch("/admin/api/notes", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRF-Token": this._csrf() },
                body: JSON.stringify({ body, in_reply_to: postId }),
            });
            if (!resp.ok) return;
            this.#replyOpen = false;
            this._render();
        } catch {
            // Network error — ignore silently
        }
    }

    async _confirmDelete(internalId) {
        if (!internalId) return;
        if (!confirm("Delete this post?")) return;
        try {
            const resp = await fetch(`/admin/api/notes/${internalId}`, {
                method: "DELETE",
                headers: { "X-CSRF-Token": this._csrf() },
            });
            if (!resp.ok) return;
            this.dispatchEvent(new CustomEvent("post-deleted", {
                bubbles: true,
                detail: { internalId },
            }));
            this.remove();
        } catch {
            // Network error — ignore silently
        }
    }
}

// ---------------------------------------------------------------------------
// Relative time
// ---------------------------------------------------------------------------

function _relativeTime(isoStr) {
    if (!isoStr) return "";
    const then = new Date(isoStr);
    const now = Date.now();
    const diff = Math.floor((now - then.getTime()) / 1000); // seconds
    if (diff < 60) return `${diff}s`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
    return `${Math.floor(diff / 86400)}d`;
}

// ---------------------------------------------------------------------------
// Inline SVG icons (Tabler Icons — MIT license)
// ---------------------------------------------------------------------------

function _svg(paths) {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"
        fill="none" stroke="currentColor" stroke-width="1.75"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}

function _iconHeart() {
    return _svg(`<path d="M19.5 12.572l-7.5 7.428l-7.5 -7.428a5 5 0 1 1 7.5 -6.566a5 5 0 1 1 7.5 6.572"/>`);
}

function _iconReply() {
    return _svg(`<path d="M3 20l1.3 -3.9a9 8 0 1 1 3.4 2.9l-4.7 1"/>`);
}

function _iconRepost() {
    return _svg(`
        <path d="M19 7l-7 -7l-7 7"/>
        <path d="M5 7v10a3 3 0 0 0 3 3h3"/>
        <path d="M5 17l7 7l7 -7"/>
        <path d="M19 17v-10a3 3 0 0 0 -3 -3h-3"/>`);
}

function _iconEdit() {
    return _svg(`
        <path d="M4 20h4l10.5 -10.5a1.5 1.5 0 0 0 -4 -4l-10.5 10.5v4"/>
        <line x1="13.5" y1="6.5" x2="17.5" y2="10.5"/>`);
}

function _iconTrash() {
    return _svg(`
        <line x1="4" y1="7" x2="20" y2="7"/>
        <line x1="10" y1="11" x2="10" y2="17"/>
        <line x1="14" y1="11" x2="14" y2="17"/>
        <path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12"/>
        <path d="M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3"/>`);
}

function _esc(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

customElements.define("status-item", StatusItem);
