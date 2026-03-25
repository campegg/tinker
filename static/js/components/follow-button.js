/**
 * <follow-button> — inline follow/unfollow toggle for a remote AP actor.
 *
 * Attributes:
 *   actor-uri  — AP URI of the remote actor (required).
 *   following  — boolean attribute; present = already following (or pending).
 *
 * Behaviour:
 *   - When `following` is absent: renders a "Follow" button.
 *     On click, POSTs to /admin/api/follow and sets the `following` attribute.
 *   - When `following` is present: renders an "Unfollow" link-button.
 *     On click, POSTs to /admin/api/unfollow and removes the `following` attribute.
 *
 * Dispatches no custom events.
 */
class FollowButton extends HTMLElement {
    static observedAttributes = ["actor-uri", "following"];

    connectedCallback() {
        this._render();
    }

    attributeChangedCallback() {
        if (this.isConnected) this._render();
    }

    _csrf() {
        return window.__TINKER__?.csrf || "";
    }

    _render() {
        const actorUri = this.getAttribute("actor-uri") || "";
        const isFollowing = this.hasAttribute("following");

        if (isFollowing) {
            this.innerHTML = `<button class="unfollow-link" type="button">Unfollow</button>`;
            this.querySelector("button").addEventListener("click", () => this._unfollow(actorUri));
        } else {
            this.innerHTML = `<button class="follow-button" type="button">Follow</button>`;
            this.querySelector("button").addEventListener("click", () => this._follow(actorUri));
        }
    }

    async _follow(actorUri) {
        const btn = this.querySelector("button");
        if (btn) btn.disabled = true;
        try {
            const resp = await fetch("/admin/api/follow", {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": this._csrf(),
                },
                body: JSON.stringify({ actor_uri: actorUri }),
            });
            if (resp.ok) {
                this.setAttribute("following", "");
            }
        } catch {
            // Network error — re-enable button
            if (btn) btn.disabled = false;
        }
    }

    async _unfollow(actorUri) {
        const btn = this.querySelector("button");
        if (btn) btn.disabled = true;
        try {
            const resp = await fetch("/admin/api/unfollow", {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": this._csrf(),
                },
                body: JSON.stringify({ actor_uri: actorUri }),
            });
            if (resp.ok) {
                this.removeAttribute("following");
            }
        } catch {
            if (btn) btn.disabled = false;
        }
    }
}

customElements.define("follow-button", FollowButton);
