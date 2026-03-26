/**
 * <search-modal> — global modal for searching Fediverse actors by handle.
 *
 * Listens on `document` for a `show-search-modal` event (dispatched by
 * <nav-bar> when the search icon is clicked). Opens a modal overlay with
 * an input for `@user@domain`; on submit fetches /admin/api/search?q={input}
 * and renders a <person-row> on match or a "No result" message on 404.
 *
 * Keyboard: Escape closes; focus is trapped inside the modal while open.
 * Click on backdrop closes.
 *
 * No attributes. No shadow DOM.
 * Attach once to the page; handles all search interactions globally.
 */
class SearchModal extends HTMLElement {
    #open = false;

    connectedCallback() {
        this._render();
        document.addEventListener("show-search-modal", () => this._show());
    }

    disconnectedCallback() {
        document.removeEventListener("show-search-modal", () => this._show());
    }

    _render() {
        this.innerHTML = `
            <div class="modal-overlay js-backdrop" hidden aria-modal="true" role="dialog"
                 aria-label="Search for a Fediverse account">
                <div class="search-modal">
                    <input
                        class="search-modal__input"
                        id="search-input"
                        type="text"
                        placeholder="Search for..."
                        autocomplete="off"
                        autocapitalize="none"
                        spellcheck="false"
                    >
                    <div class="search-modal__status" id="search-status" aria-live="polite"></div>
                    <div class="search-modal__results" id="search-results"></div>
                </div>
            </div>`;

        const overlay = this.querySelector(".modal-overlay");
        overlay?.addEventListener("click", (e) => {
            if (e.target === overlay) this._hide();
        });

        this.querySelector("#search-input")
            ?.addEventListener("keydown", (e) => {
                if (e.key === "Enter") { e.preventDefault(); this._search(); }
            });

        document.addEventListener("keydown", (e) => {
            if (this.#open && e.key === "Escape") this._hide();
        });
    }

    _show() {
        this.#open = true;
        const overlay = this.querySelector(".modal-overlay");
        if (overlay) overlay.hidden = false;
        this.querySelector("#search-input")?.focus();
    }

    _hide() {
        this.#open = false;
        const overlay = this.querySelector(".modal-overlay");
        if (overlay) overlay.hidden = true;
        this.querySelector("#search-results").innerHTML = "";
        this.querySelector("#search-status").textContent = "";
        this.querySelector("#search-input").value = "";
    }

    async _search() {
        const input = this.querySelector("#search-input");
        const status = this.querySelector("#search-status");
        const results = this.querySelector("#search-results");
        const q = input?.value?.trim() || "";
        if (!q) return;

        status.textContent = "Searching…";
        results.innerHTML = "";

        try {
            const resp = await fetch(
                `/admin/api/search?q=${encodeURIComponent(q)}`,
                { credentials: "same-origin" }
            );
            if (resp.status === 404) {
                status.textContent = "No result found.";
                return;
            }
            if (resp.status === 400) {
                status.textContent = "Enter a handle like @user@example.social";
                return;
            }
            if (!resp.ok) {
                status.textContent = "Search failed. Try again.";
                return;
            }
            const actor = await resp.json();
            status.textContent = "";
            results.innerHTML = `<person-row
                actor-uri="${_esc(actor.uri)}"
                name="${_esc(actor.display_name || actor.uri)}"
                handle="${_esc(actor.handle || "")}"
                avatar="${_esc(actor.avatar_url || "")}"
                ${actor.is_following ? "following" : ""}
            ></person-row>`;
        } catch {
            status.textContent = "Network error. Try again.";
        }
    }
}

function _esc(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

customElements.define("search-modal", SearchModal);
