"""ActivityPub delivery pipeline for fan-out to follower inboxes.

Handles persisting outbound activities to the delivery queue, dispatching
async delivery tasks bounded by a concurrency semaphore, exponential
backoff on failure, crash recovery on startup, and dead instance detection.

Architecture
------------
Delivery is fire-and-forget: callers persist queue entries via
:class:`DeliveryService`, then call :func:`dispatch_new_items` to schedule
background :func:`_deliver_task` coroutines.  Each task owns its own
database session (separate from the caller's request session) to avoid
session sharing across async task boundaries.

A module-level in-flight set deduplicates dispatch within a process
lifetime, preventing the retry loop from re-dispatching a task that is
already running.  Between restarts, :func:`startup_recovery` re-enqueues
any items that were pending when the process last exited.

Concurrency is bounded by an ``asyncio.Semaphore`` stored in
``app.config["DELIVERY_SEMAPHORE"]`` (created by the app factory).  All
outbound HTTP requests — whether first-attempt or retry — pass through
this semaphore.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from app.federation.signatures import sign_request
from app.models.delivery_queue import DeliveryQueue
from app.repositories.delivery_queue import DeliveryQueueRepository
from app.repositories.follower import FollowerRepository

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# Maximum simultaneous outbound HTTP requests for this process.
_DELIVERY_CONCURRENCY = 10

# Backoff delays (minutes) indexed by attempt number (0-based).
# Attempt 1 → 1 min, 2 → 5 min, 3 → 30 min, 4 → 2 h, 5 → 12 h.
_BACKOFF_MINUTES: list[int] = [1, 5, 30, 120, 720]

# Permanently fail a delivery after this many unsuccessful attempts.
_MAX_ATTEMPTS = 5

# Flag a follower unreachable when all deliveries to its inbox have
# failed continuously for this many days.
_DEAD_INSTANCE_DAYS = 7

# How often the background retry loop wakes to check for retryable items.
_RETRY_LOOP_INTERVAL_SECONDS = 60

# Item IDs currently dispatched in this process.  Prevents the retry loop
# from re-dispatching a task that is already in flight.
_in_flight: set[uuid.UUID] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _backoff_delay(attempt_number: int) -> timedelta:
    """Return the backoff delay for a given (1-based) attempt number.

    Args:
        attempt_number: How many attempts have been made so far
            (1 = first failure, 5 = last attempt before permanent failure).

    Returns:
        A :class:`~datetime.timedelta` representing the wait before the
        next delivery attempt.
    """
    index = min(attempt_number - 1, len(_BACKOFF_MINUTES) - 1)
    return timedelta(minutes=_BACKOFF_MINUTES[index])


# ---------------------------------------------------------------------------
# DeliveryService — persistence layer
# ---------------------------------------------------------------------------


class DeliveryService:
    """Persist and fan-out ActivityPub activities to remote inboxes.

    Handles writing queue entries to the database and computing the set
    of unique inbox URLs to deliver to.  Does *not* perform network I/O;
    use :func:`dispatch_new_items` after calling :meth:`fan_out` to
    schedule the background delivery tasks.

    Args:
        session: The async database session to use for all writes.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialise the delivery service.

        Args:
            session: The async database session to use.
        """
        self._queue_repo = DeliveryQueueRepository(session)
        self._follower_repo = FollowerRepository(session)

    async def enqueue(self, activity: dict[str, Any], inbox_url: str) -> DeliveryQueue:
        """Persist a single delivery task for one target inbox.

        The entry is flushed but not committed; call :meth:`commit` or
        use :meth:`fan_out` (which commits for the whole batch).

        Args:
            activity: The JSON-LD activity dict to deliver.
            inbox_url: The remote inbox URL to POST the activity to.

        Returns:
            The flushed :class:`~app.models.delivery_queue.DeliveryQueue`
            entry, with ``id`` and ``created_at`` populated.
        """
        entry = DeliveryQueue(
            activity_json=json.dumps(activity, ensure_ascii=False),
            target_inbox=inbox_url,
            status="pending",
            attempts=0,
        )
        return await self._queue_repo.add(entry)

    async def deliver_to_inbox(
        self,
        activity: dict[str, Any],
        inbox_url: str,
    ) -> DeliveryQueue:
        """Enqueue delivery of an activity to a single specific inbox.

        Used when a targeted reply is needed (e.g. ``Accept{Follow}``
        sent back to a new follower's inbox) rather than a full fan-out
        to all followers.

        The queue entry is committed immediately.

        Args:
            activity: The JSON-LD activity dict to deliver.
            inbox_url: The remote inbox URL to POST the activity to.

        Returns:
            The committed :class:`~app.models.delivery_queue.DeliveryQueue`
            entry.
        """
        entry = await self.enqueue(activity, inbox_url)
        await self._queue_repo.commit()
        return entry

    async def fan_out(self, activity: dict[str, Any]) -> list[DeliveryQueue]:
        """Enqueue delivery of an activity to all accepted followers.

        Deduplicates by shared inbox URL: multiple followers on the same
        instance receive a single delivery to their shared inbox rather
        than one delivery per follower.  Falls back to the personal inbox
        URL for servers without a shared inbox.

        All queue entries are committed in a single transaction.

        Args:
            activity: The JSON-LD activity dict to fan out.

        Returns:
            A list of created
            :class:`~app.models.delivery_queue.DeliveryQueue` entries,
            one per unique target inbox.
        """
        followers = await self._follower_repo.get_accepted()

        # Build a deduplicated, insertion-order-preserving inbox list.
        seen: dict[str, None] = {}
        for follower in followers:
            inbox = follower.shared_inbox_url or follower.inbox_url
            seen[inbox] = None

        entries: list[DeliveryQueue] = []
        for inbox_url in seen:
            entry = await self.enqueue(activity, inbox_url)
            entries.append(entry)

        await self._queue_repo.commit()
        return entries


# ---------------------------------------------------------------------------
# HTTP delivery — network I/O
# ---------------------------------------------------------------------------


async def _attempt_http_delivery(
    activity_json: str,
    target_inbox: str,
    *,
    private_key_pem: str,
    key_id: str,
) -> None:
    """POST an ActivityPub activity to a remote inbox with an HTTP Signature.

    Args:
        activity_json: The serialised JSON-LD activity string.
        target_inbox: The remote inbox URL to POST to.
        private_key_pem: PEM-encoded RSA private key for signing.
        key_id: The key ID URI for the HTTP Signature header
            (e.g. ``"https://example.com/user#main-key"``).

    Raises:
        httpx.HTTPStatusError: If the server returns a non-2xx status.
        httpx.RequestError: If the request could not be sent.
    """
    body = activity_json.encode("utf-8")
    sig_headers = sign_request(
        method="POST",
        url=target_inbox,
        body=body,
        private_key_pem=private_key_pem,
        key_id=key_id,
    )
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            target_inbox,
            content=body,
            headers=sig_headers,
        )
        response.raise_for_status()


# ---------------------------------------------------------------------------
# Background delivery task
# ---------------------------------------------------------------------------


async def _deliver_task(
    item_id: uuid.UUID,
    activity_json: str,
    target_inbox: str,
    current_attempts: int,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    private_key_pem: str,
    key_id: str,
    semaphore: asyncio.Semaphore,
) -> None:
    """Background coroutine: attempt delivery of one queued activity.

    Acquires the concurrency semaphore, attempts HTTP delivery, then
    updates the queue row's status in a fresh database session.

    On success: ``status`` → ``"delivered"``.
    On failure: increments ``attempts``, sets ``next_retry_at`` with
    exponential backoff, or sets ``status`` → ``"failed"`` after
    :data:`_MAX_ATTEMPTS` exhausted.

    Args:
        item_id: UUID of the :class:`~app.models.delivery_queue.DeliveryQueue` row.
        activity_json: The serialised activity to send.
        target_inbox: The remote inbox URL.
        current_attempts: The ``attempts`` value at dispatch time.
        session_factory: Factory for creating per-task database sessions.
        private_key_pem: PEM-encoded RSA private key for signing.
        key_id: Key ID URI for the Signature header.
        semaphore: Shared concurrency limiter for outbound requests.
    """
    success = False
    error_msg: str | None = None

    try:
        async with semaphore:
            await _attempt_http_delivery(
                activity_json,
                target_inbox,
                private_key_pem=private_key_pem,
                key_id=key_id,
            )
        success = True
    except httpx.HTTPStatusError as exc:
        error_msg = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.warning(
            "Delivery to %s returned HTTP %d",
            target_inbox,
            exc.response.status_code,
        )
    except httpx.RequestError as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.warning("Delivery to %s failed (network): %s", target_inbox, exc)
    except Exception:
        error_msg = "Unexpected error during delivery"
        logger.exception("Unexpected error delivering to %s", target_inbox)
    finally:
        _in_flight.discard(item_id)

    async with session_factory() as session:
        try:
            repo = DeliveryQueueRepository(session)
            item = await repo.get_by_id(item_id)
            if item is None:
                logger.warning("DeliveryQueue row %s missing during status update", item_id)
                return

            if success:
                item.status = "delivered"
                item.last_error = None
                logger.info("Delivered activity to %s (item %s)", target_inbox, item_id)
            else:
                new_attempts = current_attempts + 1
                item.attempts = new_attempts
                item.last_error = error_msg

                if new_attempts >= _MAX_ATTEMPTS:
                    item.status = "failed"
                    logger.warning(
                        "Permanently failed delivery to %s after %d attempts (item %s)",
                        target_inbox,
                        new_attempts,
                        item_id,
                    )
                    await _check_dead_instance(target_inbox, session)
                else:
                    delay = _backoff_delay(new_attempts)
                    item.next_retry_at = datetime.now(UTC) + delay
                    logger.info(
                        "Delivery attempt %d/%d to %s failed; retry in %s. Error: %s",
                        new_attempts,
                        _MAX_ATTEMPTS,
                        target_inbox,
                        delay,
                        error_msg,
                    )

            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to update delivery queue row %s after delivery attempt", item_id
            )


# ---------------------------------------------------------------------------
# Dead instance detection
# ---------------------------------------------------------------------------


async def _check_dead_instance(inbox_url: str, session: AsyncSession) -> None:
    """Flag the follower for an inbox as unreachable after persistent failure.

    Called when a delivery permanently fails (all retries exhausted).
    If no successful delivery to this inbox has occurred within the last
    :data:`_DEAD_INSTANCE_DAYS` days, the corresponding
    :class:`~app.models.follower.Follower` row is updated to
    ``status="unreachable"`` so it is excluded from future fan-outs.

    Args:
        inbox_url: The inbox URL that permanently failed.
        session: An open database session (the same one used to update the
            queue row — caller is responsible for commit).
    """
    cutoff = datetime.now(UTC) - timedelta(days=_DEAD_INSTANCE_DAYS)
    queue_repo = DeliveryQueueRepository(session)

    if await queue_repo.has_recent_success(inbox_url, since=cutoff):
        # Had at least one recent success — instance is not dead.
        return

    follower_repo = FollowerRepository(session)
    follower = await follower_repo.get_by_inbox_url(inbox_url)
    if follower is not None and follower.status == "accepted":
        follower.status = "unreachable"
        logger.warning(
            "Follower %s flagged unreachable: inbox %s has had no successful "
            "delivery in the last %d days",
            follower.actor_uri,
            inbox_url,
            _DEAD_INSTANCE_DAYS,
        )


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


def _dispatch(
    item: DeliveryQueue,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    private_key_pem: str,
    key_id: str,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Schedule a background delivery task for one queue entry.

    Skips items already in-flight within this process (tracked by
    :data:`_in_flight`).  Adds a ``done`` callback to remove the item
    from the in-flight set when the task completes.

    Args:
        item: The queue entry to deliver.
        session_factory: Factory for creating per-task DB sessions.
        private_key_pem: PEM private key for signing.
        key_id: Key ID URI for the Signature header.
        semaphore: Shared concurrency limiter.

    Returns:
        ``True`` if a new task was created; ``False`` if the item was
        already in-flight and was skipped.
    """
    if item.id in _in_flight:
        return False
    _in_flight.add(item.id)
    task = asyncio.create_task(
        _deliver_task(
            item.id,
            item.activity_json,
            item.target_inbox,
            item.attempts,
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        ),
        name=f"deliver:{item.id}",
    )
    # Belt-and-suspenders: ensure removal even if the task raises before
    # reaching the finally block (e.g. asyncio.CancelledError).
    task.add_done_callback(lambda _: _in_flight.discard(item.id))
    return True


def dispatch_new_items(
    items: list[DeliveryQueue],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    private_key_pem: str,
    key_id: str,
    semaphore: asyncio.Semaphore,
) -> int:
    """Schedule background delivery tasks for a batch of freshly enqueued items.

    Call this immediately after :meth:`DeliveryService.fan_out` to start
    delivery without blocking the caller.

    Args:
        items: The list of newly created queue entries.
        session_factory: Factory for per-task database sessions.
        private_key_pem: PEM private key for signing.
        key_id: Key ID URI for the Signature header.
        semaphore: Shared concurrency limiter.

    Returns:
        The number of tasks actually scheduled (skips already-in-flight items).
    """
    count = 0
    for item in items:
        if _dispatch(
            item,
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        ):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Startup crash recovery
# ---------------------------------------------------------------------------


async def startup_recovery(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    private_key_pem: str,
    key_id: str,
    semaphore: asyncio.Semaphore,
) -> int:
    """Re-dispatch any deliveries that were incomplete when the process last exited.

    Fetches all rows with ``status="pending"`` — both items that were
    never attempted and items whose retry window has already passed — and
    schedules them as background tasks.  Called once during app startup
    (via ``before_serving``).

    Args:
        session_factory: Factory for creating a database session.
        private_key_pem: PEM private key for signing.
        key_id: Key ID URI for the Signature header.
        semaphore: Shared concurrency limiter.

    Returns:
        The number of items recovered and dispatched.
    """
    async with session_factory() as session:
        try:
            repo = DeliveryQueueRepository(session)
            pending = await repo.get_pending()
        except Exception:
            logger.exception("Error fetching pending deliveries during startup recovery")
            return 0

    count = 0
    for item in pending:
        if _dispatch(
            item,
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        ):
            count += 1

    if count:
        logger.info("Startup recovery: dispatched %d pending deliveries", count)

    return count


# ---------------------------------------------------------------------------
# Background retry loop
# ---------------------------------------------------------------------------


async def _retry_loop_step(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    private_key_pem: str,
    key_id: str,
    semaphore: asyncio.Semaphore,
) -> None:
    """Dispatch all retryable items in one loop iteration.

    Args:
        session_factory: Factory for creating a database session.
        private_key_pem: PEM private key for signing.
        key_id: Key ID URI for the Signature header.
        semaphore: Shared concurrency limiter.
    """
    async with session_factory() as session:
        try:
            repo = DeliveryQueueRepository(session)
            retryable = await repo.get_retryable()
        except Exception:
            logger.exception("Error fetching retryable deliveries")
            return

    for item in retryable:
        _dispatch(
            item,
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        )


async def retry_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    private_key_pem: str,
    key_id: str,
    semaphore: asyncio.Semaphore,
) -> None:
    """Background loop: periodically retry deliveries whose backoff has expired.

    Sleeps for :data:`_RETRY_LOOP_INTERVAL_SECONDS` between iterations.
    Runs until cancelled (e.g. on app shutdown).

    Args:
        session_factory: Factory for creating per-iteration database sessions.
        private_key_pem: PEM private key for signing.
        key_id: Key ID URI for the Signature header.
        semaphore: Shared concurrency limiter.
    """
    logger.info("Delivery retry loop started (interval: %ds)", _RETRY_LOOP_INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.sleep(_RETRY_LOOP_INTERVAL_SECONDS)
            await _retry_loop_step(
                session_factory=session_factory,
                private_key_pem=private_key_pem,
                key_id=key_id,
                semaphore=semaphore,
            )
        except asyncio.CancelledError:
            logger.info("Delivery retry loop stopped")
            raise
        except Exception:
            logger.exception("Unexpected error in delivery retry loop; continuing")
