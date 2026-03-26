/**
 * <actor-identity> — avatar + display name + handle, grouped as a single unit.
 *
 * Attributes:
 *   src      — avatar image URL
 *   name     — display name
 *   handle   — full Fediverse handle, e.g. @user@domain
 *   size     — visual size variant: "sm" | "md" (default) | "lg"
 *
 * This is a pure display component. It never fetches data; all values must
 * be supplied as attributes by the parent component.
 *
 * Clicking the element dispatches a bubbling `show-actor-profile` CustomEvent
 * with `detail.uri` set to the `actor-uri` attribute (if present). This wires
 * up the remote actor profile modal without modifying any parent component.
 */
class ActorIdentity extends HTMLElement {
    static observedAttributes = ["src", "name", "handle", "size", "actor-uri"];

    connectedCallback() {
        this._render();
        this.addEventListener("click", this._onClick);
    }

    disconnectedCallback() {
        this.removeEventListener("click", this._onClick);
    }

    attributeChangedCallback() {
        if (this.isConnected) this._render();
    }

    _onClick = () => {
        const uri = this.getAttribute("actor-uri") || "";
        if (!uri) return;
        this.dispatchEvent(
            new CustomEvent("show-actor-profile", { bubbles: true, detail: { uri } })
        );
    };

    _render() {
        const src = this.getAttribute("src") || "";
        const name = this.getAttribute("name") || "";
        const handle = this.getAttribute("handle") || "";
        const size = this.getAttribute("size") || "md";
        const uri = this.getAttribute("actor-uri") || "";

        this.className = `actor-identity actor-identity--${size}`;
        if (uri) this.style.cursor = "pointer";

        const avatarEl = src
            ? `<img class="actor-identity__avatar" src="${_esc(src)}" alt="${_esc(name)}" loading="lazy">`
            : `<span class="actor-identity__avatar" aria-hidden="true"></span>`;

        this.innerHTML = `
            ${avatarEl}
            <div class="actor-identity__info">
                <span class="actor-identity__name">${_esc(name)}</span>
                <span class="actor-identity__handle">${_esc(handle)}</span>
            </div>`;
    }
}

/** Escape a string for safe insertion into HTML attribute values and text. */
function _esc(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

customElements.define("actor-identity", ActorIdentity);
