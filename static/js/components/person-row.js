/**
 * <person-row> — single actor row for Following/Followers list views.
 *
 * Attributes:
 *   actor-uri   — AP URI of the remote actor
 *   name        — display name
 *   handle      — @user@domain handle
 *   avatar      — avatar image URL
 *   followed    — boolean attribute; present = local user follows this actor
 *
 * Composes <actor-identity> and <follow-button>.
 * No API calls — pure display and wiring.
 */
class PersonRow extends HTMLElement {
    static observedAttributes = ["actor-uri", "name", "handle", "avatar", "followed"];

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
        const avatar = this.getAttribute("avatar") || "";
        const followed = this.hasAttribute("followed");

        this.innerHTML = `<div class="person-row">
            <actor-identity
                actor-uri="${_esc(uri)}"
                src="${_esc(avatar)}"
                name="${_esc(name)}"
                handle="${_esc(handle)}"
                size="sm"
            ></actor-identity>
            <follow-button
                actor-uri="${_esc(uri)}"
                ${followed ? "following" : ""}
            ></follow-button>
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

customElements.define("person-row", PersonRow);
