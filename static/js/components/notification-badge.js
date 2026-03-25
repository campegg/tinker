/**
 * <notification-badge> — displays the unread notification count in the nav bar.
 *
 * Lifecycle:
 *   1. On connect: fetches the current unread count from
 *      ``/admin/api/notifications/unread-count`` and renders it.
 *   2. Opens an ``EventSource`` to ``/admin/api/notifications/events``
 *      and increments the count by 1 for each incoming notification.
 *   3. Listens for a ``notifications-read`` event on ``document`` (dispatched
 *      by the notifications view after marking all read) and resets to zero.
 *
 * Rendering:
 *   - When count > 0: removes the ``hidden`` attribute and sets textContent
 *     to the numeric count.
 *   - When count === 0: sets the ``hidden`` attribute so CSS can hide it.
 *
 * Dispatches no events.  Triggers no page loads.
 */
class NotificationBadge extends HTMLElement {
    #count = 0;
    #source = null;

    connectedCallback() {
        this._fetchCount();
        this._openStream();
        document.addEventListener("notifications-read", this._onRead);
    }

    disconnectedCallback() {
        if (this.#source) {
            this.#source.close();
            this.#source = null;
        }
        document.removeEventListener("notifications-read", this._onRead);
    }

    async _fetchCount() {
        try {
            const resp = await fetch("/admin/api/notifications/unread-count");
            if (!resp.ok) return;
            const data = await resp.json();
            this.#count = typeof data.count === "number" ? data.count : 0;
            this._render();
        } catch {
            // Network error — stay hidden until next event or reconnect.
        }
    }

    _openStream() {
        const source = new EventSource("/admin/api/notifications/events");
        this.#source = source;

        source.onmessage = (e) => {
            // Ignore keep-alive pings (empty data or "ping").
            if (!e.data || e.data === "ping") return;
            this.#count += 1;
            this._render();
        };

        source.onerror = () => {
            // EventSource handles reconnection automatically per the retry
            // directive sent by the server (retry: 3000).
        };
    }

    _onRead = () => {
        this.#count = 0;
        this._render();
    };

    _render() {
        if (this.#count > 0) {
            this.textContent = String(this.#count);
            this.removeAttribute("hidden");
        } else {
            this.textContent = "";
            this.setAttribute("hidden", "");
        }
    }
}

customElements.define("notification-badge", NotificationBadge);
