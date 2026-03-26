/**
 * <actor-banner> — profile banner image with avatar at bottom-left.
 *
 * Attributes:
 *   banner-src  — URL for the banner/header image (optional)
 *   avatar-src  — URL for the avatar image (optional)
 *   mode        — "static" (default) | "editable"
 *
 * In "editable" mode, clicking the banner area or avatar triggers a file
 * picker. On successful upload to POST /admin/api/media, the component
 * dispatches a bubbling CustomEvent:
 *   - `banner-changed` with `detail.path` when the banner is replaced
 *   - `avatar-changed` with `detail.path` when the avatar is replaced
 *
 * No shadow DOM. Explicit display block set via CSS.
 */
class ActorBanner extends HTMLElement {
    static observedAttributes = ["banner-src", "avatar-src", "mode"];

    connectedCallback() {
        this._render();
    }

    attributeChangedCallback() {
        if (this.isConnected) this._render();
    }

    _render() {
        const bannerSrc = this.getAttribute("banner-src") || "";
        const avatarSrc = this.getAttribute("avatar-src") || "";
        const editable = this.getAttribute("mode") === "editable";

        const bannerImg = bannerSrc
            ? `<img class="actor-banner__bg" src="${_esc(bannerSrc)}" alt="Profile banner">`
            : `<span class="actor-banner__bg actor-banner__bg--empty" aria-hidden="true"></span>`;

        const avatarImg = avatarSrc
            ? `<img class="actor-banner__avatar" src="${_esc(avatarSrc)}" alt="Avatar">`
            : `<span class="actor-banner__avatar actor-banner__avatar--empty" aria-hidden="true"></span>`;

        const editHint = editable
            ? `<span class="actor-banner__edit-hint" aria-hidden="true">Change</span>`
            : "";

        this.innerHTML = `
            <div class="actor-banner__header${editable ? " actor-banner__header--editable" : ""}">
                ${bannerImg}
                ${editable ? editHint : ""}
            </div>
            <div class="actor-banner__avatar-wrap${editable ? " actor-banner__avatar-wrap--editable" : ""}">
                ${avatarImg}
            </div>`;

        if (editable) {
            this.querySelector(".actor-banner__header--editable")
                ?.addEventListener("click", () => this._pickFile("banner"));
            this.querySelector(".actor-banner__avatar-wrap--editable")
                ?.addEventListener("click", (e) => { e.stopPropagation(); this._pickFile("avatar"); });
        }
    }

    _pickFile(kind) {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = "image/*";
        input.addEventListener("change", async () => {
            const file = input.files?.[0];
            if (!file) return;
            await this._upload(file, kind);
        }, { once: true });
        input.click();
    }

    async _upload(file, kind) {
        const form = new FormData();
        form.append("file", file);
        try {
            const resp = await fetch("/admin/api/media", {
                method: "POST",
                credentials: "same-origin",
                headers: { "X-CSRF-Token": window.__TINKER__?.csrf || "" },
                body: form,
            });
            if (!resp.ok) return;
            const data = await resp.json();
            const path = data.path || data.url || "";
            if (!path) return;
            const eventName = kind === "banner" ? "banner-changed" : "avatar-changed";
            this.dispatchEvent(new CustomEvent(eventName, { bubbles: true, detail: { path } }));
        } catch {
            // Best-effort
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

customElements.define("actor-banner", ActorBanner);
