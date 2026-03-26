/**
 * <profile-card> — actor profile card for the remote actor modal and search results.
 *
 * Attributes:
 *   actor-uri    — AP URI of the actor
 *   name         — display name
 *   handle       — @user@domain handle
 *   avatar-src   — avatar image URL
 *   banner-src   — banner/header image URL (optional)
 *   bio          — rendered HTML biography (optional)
 *   followed     — boolean attribute; present = local user follows this actor
 *   mode         — "modal" (interactive, with follow-button) | "public" (static)
 *
 * Composes: <actor-banner mode="static">, <actor-identity>, <follow-button>.
 * Pure display component — no API calls.
 */
class ProfileCard extends HTMLElement {
    static observedAttributes = [
        "actor-uri", "name", "handle", "avatar-src", "banner-src",
        "bio", "followed", "mode",
    ];

    connectedCallback() {
        this._render();
    }

    attributeChangedCallback() {
        if (this.isConnected) this._render();
    }

    _render() {
        const uri = this.getAttribute("actor-uri") || "";
        const name = this.getAttribute("name") || uri;
        const handle = this.getAttribute("handle") || "";
        const avatarSrc = this.getAttribute("avatar-src") || "";
        const bannerSrc = this.getAttribute("banner-src") || "";
        const bio = this.getAttribute("bio") || "";
        const followed = this.hasAttribute("followed");
        const isModal = this.getAttribute("mode") !== "public";

        const followBtn = isModal
            ? `<follow-button actor-uri="${_esc(uri)}" ${followed ? "following" : ""}></follow-button>`
            : "";

        // bio contains server-sanitised HTML — inserted directly.
        const bioSection = bio
            ? `<div class="profile-card__bio">${bio}</div>`
            : "";

        this.innerHTML = `<div class="profile-card">
            <actor-banner banner-src="${_esc(bannerSrc)}" avatar-src="${_esc(avatarSrc)}" mode="static"></actor-banner>
            <div class="profile-card__body">
                <div class="profile-card__identity-row">
                    <actor-identity
                        actor-uri="${_esc(uri)}"
                        src="${_esc(avatarSrc)}"
                        name="${_esc(name)}"
                        handle="${_esc(handle)}"
                        size="md"
                    ></actor-identity>
                    ${followBtn}
                </div>
                ${bioSection}
            </div>
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

customElements.define("profile-card", ProfileCard);
