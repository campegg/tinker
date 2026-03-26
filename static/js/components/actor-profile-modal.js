/**
 * <actor-profile-modal> — global modal that shows a remote actor's profile.
 *
 * Listens on `document` for `show-actor-profile` CustomEvent (dispatched by
 * <actor-identity> on click). Fetches GET /admin/api/actor?uri={uri} and
 * renders a <profile-card mode="modal">.
 *
 * Keyboard: Escape closes. Clicking the backdrop closes.
 *
 * No attributes. No shadow DOM.
 * Attach once per page; handles all actor-profile triggers globally.
 */
class ActorProfileModal extends HTMLElement {
    #open = false;

    connectedCallback() {
        this._render();
        document.addEventListener("show-actor-profile", (e) => {
            const uri = e.detail?.uri;
            if (uri) this._show(uri);
        });
        document.addEventListener("keydown", (e) => {
            if (this.#open && e.key === "Escape") this._hide();
        });
    }

    _render() {
        this.innerHTML = `
            <div class="modal-overlay js-backdrop" hidden aria-modal="true" role="dialog"
                 aria-label="Actor profile">
                <div class="actor-profile-modal">
                    <button class="modal-overlay__close js-close" aria-label="Close">&#x2715;</button>
                    <div class="actor-profile-modal__body" id="actor-profile-body">
                        <div class="actor-profile-modal__loading">Loading…</div>
                    </div>
                </div>
            </div>`;

        const overlay = this.querySelector(".modal-overlay");
        overlay?.addEventListener("click", (e) => {
            if (e.target === overlay) this._hide();
        });
        this.querySelector(".js-close")
            ?.addEventListener("click", () => this._hide());
    }

    async _show(uri) {
        this.#open = true;
        const overlay = this.querySelector(".modal-overlay");
        const body = this.querySelector("#actor-profile-body");
        if (overlay) overlay.hidden = false;
        if (body) body.innerHTML = `<div class="actor-profile-modal__loading">Loading…</div>`;

        try {
            const resp = await fetch(
                `/admin/api/actor?uri=${encodeURIComponent(uri)}`,
                { credentials: "same-origin" }
            );
            if (!resp.ok) {
                if (body) body.innerHTML = `<div class="actor-profile-modal__error">Could not load profile.</div>`;
                return;
            }
            const actor = await resp.json();
            if (body) {
                body.innerHTML = `<profile-card
                    mode="modal"
                    actor-uri="${_esc(actor.uri)}"
                    name="${_esc(actor.display_name || actor.uri)}"
                    handle="${_esc(actor.handle || "")}"
                    avatar-src="${_esc(actor.avatar_url || "")}"
                    banner-src="${_esc(actor.header_image_url || "")}"
                    bio="${_esc(actor.bio || "")}"
                    ${actor.is_following ? "followed" : ""}
                ></profile-card>`;
            }
        } catch {
            if (body) body.innerHTML = `<div class="actor-profile-modal__error">Network error.</div>`;
        }
    }

    _hide() {
        this.#open = false;
        const overlay = this.querySelector(".modal-overlay");
        if (overlay) overlay.hidden = true;
    }
}

function _esc(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

customElements.define("actor-profile-modal", ActorProfileModal);
