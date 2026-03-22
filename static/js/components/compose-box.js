/**
 * <compose-box> — post compose form with optional image attachment.
 *
 * Attributes:
 *   in-reply-to  — AP URI of the post being replied to (optional)
 *
 * Fires:
 *   post-submitted  — when the post is successfully published;
 *                     detail: { id, ap_id }
 *
 * Image attachments (WP-14): upload via /admin/api/media before posting,
 * then include attachment_ids in the create-note request body.
 */
class ComposeBox extends HTMLElement {
    /** @type {Array<{ id: string, url: string }>} */
    #attachments = [];
    #submitting = false;

    connectedCallback() {
        this._render();
    }

    _csrf() {
        return window.__TINKER__?.csrf || "";
    }

    _render() {
        const inReplyTo = this.getAttribute("in-reply-to") || "";

        this.innerHTML = `
            <div class="compose-box">
                <textarea
                    class="compose-box__field js-body"
                    placeholder="What's happening?"
                    aria-label="Compose a post"
                ></textarea>
                <div class="compose-box__toolbar">
                    <div class="compose-box__toolbar-start">
                        <button class="compose-box__icon-btn js-attach" type="button" aria-label="Attach image">
                            ${_iconPhoto()}
                        </button>
                        <input
                            class="js-file-input"
                            type="file"
                            accept="image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif"
                            multiple
                            style="display:none"
                            aria-hidden="true"
                        >
                    </div>
                    <button class="compose-box__btn js-post" type="button">Post</button>
                </div>
                <div class="compose-box__previews js-previews"></div>
            </div>`;

        this.querySelector(".js-attach").addEventListener("click", () => {
            this.querySelector(".js-file-input").click();
        });

        this.querySelector(".js-file-input").addEventListener("change", (e) => {
            const files = Array.from(e.target.files || []);
            files.forEach(f => this._uploadFile(f));
            e.target.value = "";
        });

        this.querySelector(".js-post").addEventListener("click", () => {
            const body = this.querySelector(".js-body").value.trim();
            if (!body || this.#submitting) return;
            this._submit(body, inReplyTo);
        });

        this.querySelector(".js-body").addEventListener("keydown", (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                const body = this.querySelector(".js-body").value.trim();
                if (!body || this.#submitting) return;
                this._submit(body, inReplyTo);
            }
        });
    }

    async _uploadFile(file) {
        const formData = new FormData();
        formData.append("file", file);

        // Show a loading preview immediately using a local object URL.
        const localUrl = URL.createObjectURL(file);
        const tempId = `tmp-${Date.now()}-${Math.random()}`;
        this.#attachments.push({ id: tempId, url: localUrl, uploading: true });
        this._renderPreviews();

        try {
            const resp = await fetch("/admin/api/media", {
                method: "POST",
                headers: { "X-CSRF-Token": this._csrf() },
                body: formData,
            });
            URL.revokeObjectURL(localUrl);

            if (!resp.ok) {
                this.#attachments = this.#attachments.filter(a => a.id !== tempId);
                this._renderPreviews();
                return;
            }

            const data = await resp.json();
            // Replace the temp entry with the real server ID and URL.
            const idx = this.#attachments.findIndex(a => a.id === tempId);
            if (idx !== -1) {
                this.#attachments[idx] = { id: data.id, url: data.url, uploading: false };
            }
        } catch {
            URL.revokeObjectURL(localUrl);
            this.#attachments = this.#attachments.filter(a => a.id !== tempId);
        }

        this._renderPreviews();
    }

    _renderPreviews() {
        const container = this.querySelector(".js-previews");
        if (!container) return;
        container.innerHTML = this.#attachments.map(a => `
            <div class="compose-box__preview">
                <img src="${_esc(a.url)}" alt="">
                ${a.uploading
                    ? `<span class="compose-box__preview-remove" aria-label="Uploading...">…</span>`
                    : `<button class="compose-box__preview-remove js-remove-attachment" data-id="${_esc(a.id)}" aria-label="Remove attachment">&times;</button>`
                }
            </div>`).join("");

        container.querySelectorAll(".js-remove-attachment").forEach(btn => {
            btn.addEventListener("click", () => {
                const id = btn.getAttribute("data-id");
                this.#attachments = this.#attachments.filter(a => a.id !== id);
                this._renderPreviews();
            });
        });
    }

    async _submit(body, inReplyTo) {
        this.#submitting = true;
        const btn = this.querySelector(".js-post");
        if (btn) btn.disabled = true;

        const readyIds = this.#attachments
            .filter(a => !a.uploading)
            .map(a => a.id);

        try {
            const payload = { body };
            if (inReplyTo) payload.in_reply_to = inReplyTo;
            if (readyIds.length) payload.attachment_ids = readyIds;

            const resp = await fetch("/admin/api/notes", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": this._csrf(),
                },
                body: JSON.stringify(payload),
            });

            if (!resp.ok) return;

            const data = await resp.json();
            this.querySelector(".js-body").value = "";
            this.#attachments = [];
            this._renderPreviews();

            this.dispatchEvent(new CustomEvent("post-submitted", {
                bubbles: true,
                detail: { id: data.id, ap_id: data.ap_id },
            }));
        } catch {
            // Network error — re-enable the button so the user can retry
        } finally {
            this.#submitting = false;
            if (btn) btn.disabled = false;
        }
    }
}

function _iconPhoto() {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"
        fill="none" stroke="currentColor" stroke-width="1.75"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M15 8h.01"/>
        <rect x="3" y="6" width="18" height="13" rx="2"/>
        <path d="M3 16l5 -5c.928 -.893 2.072 -.893 3 0l5 5"/>
        <path d="M14 14l1 -1c.928 -.893 2.072 -.893 3 0l3 3"/>
    </svg>`;
}

function _esc(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

customElements.define("compose-box", ComposeBox);
