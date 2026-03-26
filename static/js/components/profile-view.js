/**
 * <profile-view> — container component for the admin profile edit page.
 *
 * On connection:
 *   1. Fetches GET /admin/api/profile.
 *   2. Renders an <actor-banner mode="editable"> + 2-column edit form.
 *   3. Listens for banner-changed / avatar-changed events to queue path updates.
 *   4. On save: PATCHes /admin/api/profile with changed fields.
 *
 * No attributes. No shadow DOM.
 */
class ProfileView extends HTMLElement {
    #profile = null;
    #pendingAvatarPath = null;
    #pendingHeaderPath = null;

    connectedCallback() {
        this.innerHTML = `<div class="profile-view__loading">Loading\u2026</div>`;
        this._load();
    }

    async _load() {
        try {
            const resp = await fetch("/admin/api/profile", { credentials: "same-origin" });
            if (!resp.ok) throw new Error(`API error ${resp.status}`);
            this.#profile = await resp.json();
            this._render();
        } catch {
            this.innerHTML = `<div class="profile-view__error">Failed to load profile.</div>`;
        }
    }

    _render() {
        const p = this.#profile;

        // Decompose handle "@cam@campegg.com" -> user "@cam", domain "@campegg.com"
        const handle = p.handle || "";
        const parts = handle.replace(/^@/, "").split("@");
        const handleUser = parts[0] ? `@${parts[0]}` : handle;
        const handleDomain = parts[1] ? `@${parts[1]}` : "";

        this.innerHTML = `
            <actor-banner
                banner-src="${_esc(p.header_image_url || "")}"
                avatar-src="${_esc(p.avatar_url || "")}"
                mode="editable"
            ></actor-banner>
            <form class="profile-view__form" id="profile-form">
                <label class="profile-view__label" for="pv-name">Display name</label>
                <input
                    class="profile-view__input"
                    id="pv-name"
                    name="display_name"
                    type="text"
                    value="${_esc(p.display_name || "")}"
                    autocomplete="name"
                >

                <label class="profile-view__label" for="pv-handle">Handle</label>
                <div class="profile-view__handle-wrap">
                    <input
                        class="profile-view__input profile-view__handle-input"
                        id="pv-handle"
                        type="text"
                        value="${_esc(handleUser)}"
                        readonly
                        aria-readonly="true"
                    >
                    <span class="profile-view__handle-suffix">${_esc(handleDomain)}</span>
                </div>

                <label class="profile-view__label" for="pv-bio">Bio</label>
                <textarea
                    class="profile-view__textarea"
                    id="pv-bio"
                    name="bio"
                    rows="4"
                >${_escText(p.bio || "")}</textarea>

                <label class="profile-view__label" for="pv-links">Links</label>
                <textarea
                    class="profile-view__textarea"
                    id="pv-links"
                    name="links"
                    rows="3"
                    placeholder="One URL per line"
                >${_escText((p.links || []).join("\n"))}</textarea>

                <div class="profile-view__actions">
                    <button type="button" class="profile-view__cancel-btn" id="pv-cancel">Cancel</button>
                    <button type="submit" class="profile-view__save-btn">Save</button>
                </div>
                <span class="profile-view__status" id="pv-status" aria-live="polite"></span>
            </form>`;

        this.addEventListener("banner-changed", (e) => {
            this.#pendingHeaderPath = e.detail.path;
        });
        this.addEventListener("avatar-changed", (e) => {
            this.#pendingAvatarPath = e.detail.path;
        });

        this.querySelector("#profile-form")
            ?.addEventListener("submit", (e) => { e.preventDefault(); this._save(); });

        this.querySelector("#pv-cancel")
            ?.addEventListener("click", () => this._load());
    }

    async _save() {
        const form = this.querySelector("#profile-form");
        const status = this.querySelector("#pv-status");
        if (!form) return;

        const payload = {
            display_name: form.querySelector("[name=display_name]")?.value ?? "",
            bio: form.querySelector("[name=bio]")?.value ?? "",
            links: (form.querySelector("[name=links]")?.value ?? "")
                .split("\n")
                .map((l) => l.trim())
                .filter(Boolean),
        };

        if (this.#pendingAvatarPath !== null) {
            payload.avatar_path = this.#pendingAvatarPath;
        }
        if (this.#pendingHeaderPath !== null) {
            payload.header_image_path = this.#pendingHeaderPath;
        }

        if (status) status.textContent = "Saving\u2026";

        try {
            const resp = await fetch("/admin/api/profile", {
                method: "PATCH",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": window.__TINKER__?.csrf || "",
                },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                if (status) status.textContent = err.error || "Save failed.";
                return;
            }
            this.#pendingAvatarPath = null;
            this.#pendingHeaderPath = null;
            if (status) {
                status.textContent = "Saved.";
                setTimeout(() => { if (status) status.textContent = ""; }, 3000);
            }
        } catch {
            if (status) status.textContent = "Network error.";
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

function _escText(str) {
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

customElements.define("profile-view", ProfileView);
