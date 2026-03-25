/**
 * <notification-item> — renders a single notification row.
 *
 * Attributes:
 *   type          — notification type: "follow" | "like" | "boost" | "reply"
 *   actor-uri     — AP URI of the actor who triggered the notification
 *   actor-name    — display name of the actor
 *   actor-handle  — @user@domain handle string
 *   actor-avatar  — URL of the actor's (proxied) avatar
 *   object-uri    — AP URI of the relevant post (for like/boost/reply)
 *   content       — sanitised HTML body of the reply (reply type only)
 *   is-following  — boolean attribute; present = local user follows this actor
 *   published     — ISO 8601 timestamp (for reply <status-item>)
 *
 * Variants:
 *   follow  — actor identity + follow/unfollow toggle
 *   like    — actor identity + "liked your post"
 *   boost   — actor identity + "boosted your post"
 *   reply   — actor identity + reply card (<status-item>)
 *
 * Dispatches no events.
 */
class NotificationItem extends HTMLElement {
    static observedAttributes = [
        "type", "actor-uri", "actor-name", "actor-handle",
        "actor-avatar", "object-uri", "content", "is-following", "published",
    ];

    connectedCallback() {
        this._render();
    }

    attributeChangedCallback() {
        if (this.isConnected) this._render();
    }

    _render() {
        const type       = this.getAttribute("type") || "";
        const actorUri   = this.getAttribute("actor-uri") || "";
        const actorName  = this.getAttribute("actor-name") || actorUri;
        const actorHandle = this.getAttribute("actor-handle") || "";
        const actorAvatar = this.getAttribute("actor-avatar") || "";
        const objectUri  = this.getAttribute("object-uri") || "";
        const content    = this.getAttribute("content") || "";
        const isFollowing = this.hasAttribute("is-following");
        const published  = this.getAttribute("published") || "";

        const actorHtml = `<actor-identity
            name="${_esc(actorName)}"
            handle="${_esc(actorHandle)}"
            avatar="${_esc(actorAvatar)}"
            size="sm"
        ></actor-identity>`;

        let actionHtml = "";
        let bodyHtml = "";

        switch (type) {
            case "follow":
                actionHtml = isFollowing
                    ? `<follow-button actor-uri="${_esc(actorUri)}" following></follow-button>`
                    : `<follow-button actor-uri="${_esc(actorUri)}"></follow-button>`;
                break;

            case "like":
                actionHtml = `<span class="notification-item__label">liked your post</span>`;
                break;

            case "boost":
                actionHtml = `<span class="notification-item__label">boosted your post</span>`;
                break;

            case "reply":
                actionHtml = `<span class="notification-item__label">replied</span>`;
                bodyHtml = `<div class="notification-item__reply-card">
                    <status-item
                        post-id="${_esc(objectUri)}"
                        author-name="${_esc(actorName)}"
                        author-handle="${_esc(actorHandle)}"
                        author-avatar="${_esc(actorAvatar)}"
                        published="${_esc(published)}"
                        body="${_esc(content)}"
                    ></status-item>
                </div>`;
                break;
        }

        this.innerHTML = `<div class="notification-item">
            <div class="notification-item__header">
                <div class="notification-item__meta">
                    ${actorHtml}
                </div>
                ${actionHtml}
            </div>
            ${bodyHtml}
        </div>`;
    }
}

function _esc(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

customElements.define("notification-item", NotificationItem);
