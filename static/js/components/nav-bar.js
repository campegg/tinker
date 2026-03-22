/**
 * <nav-bar> — primary admin navigation bar.
 *
 * Attributes:
 *   active  — name of the currently active view: "timeline" | "profile" |
 *             "notifications" | "likes" | "following" | "followers"
 *
 * Contains a <notification-badge> slot for the WP-16 badge; until that
 * component is defined, the slot renders as an empty inline element.
 *
 * Dispatches no events. Triggers a full page load on link click.
 */
class NavBar extends HTMLElement {
    static observedAttributes = ["active"];
</thinking>

    connectedCallback() {
        this._render();
    }

    attributeChangedCallback() {
        if (this.isConnected) this._render();
    }

    _render() {
        const active = this.getAttribute("active") || "";

        const links = [
            { key: "timeline",      label: "Home",          href: "/admin/timeline" },
            { key: "profile",       label: "Profile",       href: "/admin/profile" },
            { key: "notifications", label: "Notifications", href: "/admin/notifications", badge: true },
            { key: "likes",         label: "Likes",         href: "/admin/likes" },
            { key: "following",     label: "Following",     href: "/admin/following" },
            { key: "followers",     label: "Followers",     href: "/admin/followers" },
        ];

        const linksHtml = links.map(({ key, label, href, badge }) => {
            const isActive = key === active;
            const badgeHtml = badge
                ? `<notification-badge></notification-badge>`
                : "";
            const content = badge
                ? `<span class="admin-nav__badge-wrap">${_esc(label)}${badgeHtml}</span>`
                : _esc(label);
            return `<a class="admin-nav__item" href="${href}" data-active="${isActive}">${content}</a>`;
        }).join("");

        this.innerHTML = `
            <nav class="admin-nav">
                ${linksHtml}
                <span class="admin-nav__spacer"></span>
                <button class="admin-nav__search" id="search-trigger" aria-label="Search">
                    ${_iconSearch()}
                </button>
            </nav>`;

        this.querySelector("#search-trigger")
            ?.addEventListener("click", () => {
                document.dispatchEvent(new CustomEvent("show-search-modal", { bubbles: true }));
            });
    }
}

function _esc(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function _iconSearch() {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24"
        fill="none" stroke="currentColor" stroke-width="1.75"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="10" cy="10" r="7"/>
        <line x1="21" y1="21" x2="15" y2="15"/>
    </svg>`;
}

customElements.define("nav-bar", NavBar);
