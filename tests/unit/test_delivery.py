"""Unit tests for the ActivityPub delivery pipeline.

Tests cover:
- DeliveryService.enqueue and fan_out with shared-inbox deduplication
- Backoff delay calculation
- _deliver_task status updates on success and failure
- Dead instance detection logic
- dispatch_new_items in-flight deduplication
- startup_recovery dispatches pending items
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.federation.delivery import (
    _BACKOFF_MINUTES,
    _MAX_ATTEMPTS,
    DeliveryService,
    _backoff_delay,
    _deliver_task,
    _in_flight,
    dispatch_new_items,
    startup_recovery,
)
from app.models.delivery_queue import DeliveryQueue
from app.models.follower import Follower

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_follower(
    inbox_url: str,
    shared_inbox_url: str | None = None,
) -> Follower:
    """Build a minimal accepted Follower for testing."""
    f = Follower(
        id=uuid.uuid4(),
        actor_uri=f"https://remote.example.com/users/{uuid.uuid4().hex[:8]}",
        inbox_url=inbox_url,
        shared_inbox_url=shared_inbox_url,
        status="accepted",
    )
    return f


def _make_queue_item(
    inbox_url: str = "https://remote.example.com/inbox",
    attempts: int = 0,
    next_retry_at: datetime | None = None,
) -> DeliveryQueue:
    """Build a minimal pending DeliveryQueue row for testing."""
    item = DeliveryQueue(
        id=uuid.uuid4(),
        activity_json=json.dumps({"type": "Create"}),
        target_inbox=inbox_url,
        status="pending",
        attempts=attempts,
        next_retry_at=next_retry_at,
    )
    return item


def _make_semaphore() -> asyncio.Semaphore:
    return asyncio.Semaphore(10)


def _make_session_factory() -> Any:
    """Return a mock async_sessionmaker that yields a mock AsyncSession."""
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock()
    factory.return_value = mock_session
    return factory, mock_session


# ---------------------------------------------------------------------------
# Backoff delay
# ---------------------------------------------------------------------------


class TestBackoffDelay:
    """Verify the backoff schedule produces the correct delays."""

    def test_first_failure_is_one_minute(self) -> None:
        assert _backoff_delay(1) == timedelta(minutes=_BACKOFF_MINUTES[0])

    def test_second_failure_is_five_minutes(self) -> None:
        assert _backoff_delay(2) == timedelta(minutes=_BACKOFF_MINUTES[1])

    def test_last_failure_caps_at_twelve_hours(self) -> None:
        assert _backoff_delay(_MAX_ATTEMPTS) == timedelta(minutes=_BACKOFF_MINUTES[-1])

    def test_beyond_max_still_caps(self) -> None:
        assert _backoff_delay(99) == timedelta(minutes=_BACKOFF_MINUTES[-1])

    def test_all_delays_are_strictly_increasing(self) -> None:
        delays = [_backoff_delay(i + 1) for i in range(len(_BACKOFF_MINUTES))]
        assert delays == sorted(delays)


# ---------------------------------------------------------------------------
# DeliveryService.fan_out — shared inbox deduplication
# ---------------------------------------------------------------------------


class TestDeliveryServiceFanOut:
    """DeliveryService.fan_out deduplicates by shared inbox."""

    async def test_fan_out_no_followers_returns_empty(self, mock_session: Any) -> None:
        """No followers → no queue entries created."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        svc = DeliveryService(mock_session)
        entries = await svc.fan_out({"type": "Create"})

        assert entries == []

    async def test_fan_out_two_followers_same_shared_inbox(self, mock_session: Any) -> None:
        """Two followers sharing one inbox → one delivery entry."""
        shared = "https://shared.example.com/inbox"
        followers = [
            _make_follower("https://shared.example.com/users/a/inbox", shared),
            _make_follower("https://shared.example.com/users/b/inbox", shared),
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = followers
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        # Patch enqueue() to return the entry unchanged.
        async def _fake_enqueue(self_: Any, activity: Any, inbox_url: str) -> DeliveryQueue:
            return _make_queue_item(inbox_url)

        with patch.object(DeliveryService, "enqueue", _fake_enqueue):
            svc = DeliveryService(mock_session)
            entries = await svc.fan_out({"type": "Create"})

        assert len(entries) == 1

    async def test_fan_out_two_followers_different_inboxes(self, mock_session: Any) -> None:
        """Two followers on different servers → two delivery entries."""
        followers = [
            _make_follower("https://alpha.example.com/inbox"),
            _make_follower("https://beta.example.com/inbox"),
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = followers
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        created_entries: list[DeliveryQueue] = []

        async def _fake_enqueue(self_: Any, activity: Any, inbox_url: str) -> DeliveryQueue:
            entry = _make_queue_item(inbox_url)
            created_entries.append(entry)
            return entry

        with patch.object(DeliveryService, "enqueue", _fake_enqueue):
            svc = DeliveryService(mock_session)
            entries = await svc.fan_out({"type": "Create"})

        assert len(entries) == 2
        inboxes = {e.target_inbox for e in entries}
        assert "https://alpha.example.com/inbox" in inboxes
        assert "https://beta.example.com/inbox" in inboxes

    async def test_fan_out_prefers_shared_over_personal_inbox(self, mock_session: Any) -> None:
        """Shared inbox URL is used when available; personal inbox is skipped."""
        shared = "https://shared.example.com/inbox"
        personal = "https://shared.example.com/users/alice/inbox"
        followers = [_make_follower(personal, shared)]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = followers
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        created_inboxes: list[str] = []

        async def _fake_enqueue(self_: Any, activity: Any, inbox_url: str) -> DeliveryQueue:
            created_inboxes.append(inbox_url)
            return _make_queue_item(inbox_url)

        with patch.object(DeliveryService, "enqueue", _fake_enqueue):
            svc = DeliveryService(mock_session)
            await svc.fan_out({"type": "Create"})

        assert created_inboxes == [shared]

    async def test_fan_out_activity_json_is_serialised(self, mock_session: Any) -> None:
        """The activity dict is serialised to JSON in the queue entry."""
        follower = _make_follower("https://remote.example.com/inbox")
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [follower]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        activity = {"type": "Create", "actor": "https://local.example.com/alice"}
        svc = DeliveryService(mock_session)
        entries = await svc.fan_out(activity)

        assert len(entries) == 1
        assert json.loads(entries[0].activity_json) == activity


# ---------------------------------------------------------------------------
# _deliver_task status updates
# ---------------------------------------------------------------------------


class TestDeliverTaskStatusUpdates:
    """_deliver_task updates queue row status correctly."""

    async def test_success_marks_delivered(self) -> None:
        """A 2xx response marks the item as delivered."""
        item_id = uuid.uuid4()
        item = _make_queue_item()
        item.id = item_id

        factory, session_mock = _make_session_factory()

        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=item)

        with (
            patch(
                "app.federation.delivery._attempt_http_delivery",
                new=AsyncMock(),
            ),
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_repo,
            ),
        ):
            _in_flight.add(item_id)
            await _deliver_task(
                item_id,
                item.activity_json,
                item.target_inbox,
                0,
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

        assert item.status == "delivered"
        assert item.last_error is None
        session_mock.commit.assert_called_once()

    async def test_failure_increments_attempts_and_sets_retry(self) -> None:
        """An HTTP error increments attempts and schedules a retry."""
        import httpx

        item_id = uuid.uuid4()
        item = _make_queue_item(attempts=0)
        item.id = item_id

        factory, mock_session = _make_session_factory()
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=item)

        fake_response = MagicMock()
        fake_response.status_code = 500
        fake_response.text = "Server Error"
        http_error = httpx.HTTPStatusError("500", request=MagicMock(), response=fake_response)

        with (
            patch(
                "app.federation.delivery._attempt_http_delivery",
                new=AsyncMock(side_effect=http_error),
            ),
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_repo,
            ),
        ):
            _in_flight.add(item_id)
            await _deliver_task(
                item_id,
                item.activity_json,
                item.target_inbox,
                0,
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

        assert item.attempts == 1
        assert item.status == "pending"
        assert item.next_retry_at is not None
        assert item.last_error is not None
        mock_session.commit.assert_called_once()

    async def test_max_attempts_marks_failed(self) -> None:
        """Reaching max attempts sets status to 'failed'."""
        import httpx

        item_id = uuid.uuid4()
        item = _make_queue_item(attempts=_MAX_ATTEMPTS - 1)
        item.id = item_id

        factory, _session_mock = _make_session_factory()
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=item)

        fake_response = MagicMock()
        fake_response.status_code = 503
        fake_response.text = "Unavailable"
        http_error = httpx.HTTPStatusError("503", request=MagicMock(), response=fake_response)

        mock_dead_check = AsyncMock()

        with (
            patch(
                "app.federation.delivery._attempt_http_delivery",
                new=AsyncMock(side_effect=http_error),
            ),
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_repo,
            ),
            patch(
                "app.federation.delivery._check_dead_instance",
                new=mock_dead_check,
            ),
        ):
            _in_flight.add(item_id)
            await _deliver_task(
                item_id,
                item.activity_json,
                item.target_inbox,
                _MAX_ATTEMPTS - 1,
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

        assert item.status == "failed"
        assert item.attempts == _MAX_ATTEMPTS
        mock_dead_check.assert_called_once()

    async def test_missing_queue_item_is_handled_gracefully(self) -> None:
        """If the queue row is gone by task time, no error is raised."""
        item_id = uuid.uuid4()

        factory, _ = _make_session_factory()
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=None)

        with (
            patch(
                "app.federation.delivery._attempt_http_delivery",
                new=AsyncMock(),
            ),
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_repo,
            ),
        ):
            _in_flight.add(item_id)
            # Should not raise.
            await _deliver_task(
                item_id,
                json.dumps({"type": "Create"}),
                "https://remote.example.com/inbox",
                0,
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

    async def test_item_removed_from_in_flight_on_success(self) -> None:
        """Item ID is removed from _in_flight after the task completes."""
        item_id = uuid.uuid4()
        item = _make_queue_item()
        item.id = item_id

        factory, _ = _make_session_factory()
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=item)

        _in_flight.add(item_id)

        with (
            patch("app.federation.delivery._attempt_http_delivery", new=AsyncMock()),
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_repo,
            ),
        ):
            await _deliver_task(
                item_id,
                item.activity_json,
                item.target_inbox,
                0,
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

        assert item_id not in _in_flight

    async def test_item_removed_from_in_flight_on_failure(self) -> None:
        """Item ID is removed from _in_flight even after network failure."""
        import httpx

        item_id = uuid.uuid4()
        item = _make_queue_item()
        item.id = item_id

        factory, _ = _make_session_factory()
        mock_repo = AsyncMock()
        mock_repo.get_by_id = AsyncMock(return_value=item)

        _in_flight.add(item_id)

        with (
            patch(
                "app.federation.delivery._attempt_http_delivery",
                new=AsyncMock(side_effect=httpx.ConnectError("refused")),
            ),
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_repo,
            ),
        ):
            await _deliver_task(
                item_id,
                item.activity_json,
                item.target_inbox,
                0,
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

        assert item_id not in _in_flight


# ---------------------------------------------------------------------------
# dispatch_new_items — in-flight deduplication
# ---------------------------------------------------------------------------


class TestDispatchNewItems:
    """dispatch_new_items skips already-in-flight items."""

    def test_dispatches_new_items(self) -> None:
        """Fresh items are dispatched and added to _in_flight."""
        items = [_make_queue_item(), _make_queue_item()]
        factory, _ = _make_session_factory()
        semaphore = _make_semaphore()

        with patch("app.federation.delivery.asyncio.create_task") as mock_ct:
            mock_task = MagicMock()
            mock_task.add_done_callback = MagicMock()
            mock_ct.return_value = mock_task

            count = dispatch_new_items(
                items,
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=semaphore,
            )

        assert count == 2
        assert mock_ct.call_count == 2
        # Close unawaited coroutines that were passed to the mocked create_task.
        # asyncio.create_task was patched so the coroutines were never scheduled;
        # without explicit close() Python's GC emits "coroutine was never awaited"
        # warnings when it collects them during a later test.
        for call in mock_ct.call_args_list:
            coro = call.args[0]
            coro.close()
        # Clean up in-flight tracking.
        for item in items:
            _in_flight.discard(item.id)

    def test_skips_already_in_flight(self) -> None:
        """Items already in _in_flight are not re-dispatched."""
        item = _make_queue_item()
        _in_flight.add(item.id)
        factory, _ = _make_session_factory()

        with patch("app.federation.delivery.asyncio.create_task") as mock_ct:
            count = dispatch_new_items(
                [item],
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

        assert count == 0
        mock_ct.assert_not_called()
        _in_flight.discard(item.id)


# ---------------------------------------------------------------------------
# startup_recovery
# ---------------------------------------------------------------------------


class TestStartupRecovery:
    """startup_recovery dispatches all pending items on startup."""

    async def test_dispatches_pending_items(self) -> None:
        """Pending items are dispatched during startup recovery."""
        items = [_make_queue_item(), _make_queue_item()]

        factory, _session_mock = _make_session_factory()

        mock_repo = AsyncMock()
        mock_repo.get_pending = AsyncMock(return_value=items)

        with (
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_repo,
            ),
            patch("app.federation.delivery.asyncio.create_task") as mock_ct,
        ):
            mock_task = MagicMock()
            mock_task.add_done_callback = MagicMock()
            mock_ct.return_value = mock_task

            count = await startup_recovery(
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

        assert count == 2
        assert mock_ct.call_count == 2
        # Close unawaited coroutines that were passed to the mocked create_task.
        # asyncio.create_task was patched so the coroutines were never scheduled;
        # without explicit close() Python's GC emits "coroutine was never awaited"
        # warnings when it collects them during the next test.
        for call in mock_ct.call_args_list:
            coro = call.args[0]
            coro.close()
        # Clean up in-flight tracking.
        for item in items:
            _in_flight.discard(item.id)

    async def test_returns_zero_when_no_pending_items(self) -> None:
        """Returns 0 when there are no pending deliveries to recover."""
        factory, _ = _make_session_factory()
        mock_repo = AsyncMock()
        mock_repo.get_pending = AsyncMock(return_value=[])

        with patch(
            "app.federation.delivery.DeliveryQueueRepository",
            return_value=mock_repo,
        ):
            count = await startup_recovery(
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

        assert count == 0

    async def test_handles_db_error_gracefully(self) -> None:
        """A database error during recovery is logged, not raised."""
        factory, _ = _make_session_factory()
        mock_repo = AsyncMock()
        mock_repo.get_pending = AsyncMock(side_effect=RuntimeError("DB error"))

        with patch(
            "app.federation.delivery.DeliveryQueueRepository",
            return_value=mock_repo,
        ):
            count = await startup_recovery(
                session_factory=factory,
                private_key_pem="key",
                key_id="kid",
                semaphore=_make_semaphore(),
            )

        assert count == 0


# ---------------------------------------------------------------------------
# Dead instance detection
# ---------------------------------------------------------------------------


class TestDeadInstanceDetection:
    """_check_dead_instance flags followers as unreachable correctly."""

    async def test_no_action_when_recent_success_exists(self) -> None:
        """Does not flag follower when a recent successful delivery exists."""
        from app.federation.delivery import _check_dead_instance

        mock_session = AsyncMock()
        mock_queue_repo = AsyncMock()
        mock_queue_repo.has_recent_success = AsyncMock(return_value=True)
        mock_follower_repo = AsyncMock()

        with (
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_queue_repo,
            ),
            patch(
                "app.federation.delivery.FollowerRepository",
                return_value=mock_follower_repo,
            ),
        ):
            await _check_dead_instance("https://dead.example.com/inbox", mock_session)

        mock_follower_repo.get_by_inbox_url.assert_not_called()

    async def test_flags_follower_as_unreachable_when_no_recent_success(self) -> None:
        """Marks follower unreachable when inbox has had no recent success."""
        from app.federation.delivery import _check_dead_instance

        follower = _make_follower("https://dead.example.com/inbox")
        follower.status = "accepted"

        mock_session = AsyncMock()
        mock_queue_repo = AsyncMock()
        mock_queue_repo.has_recent_success = AsyncMock(return_value=False)

        mock_follower_repo = AsyncMock()
        mock_follower_repo.get_by_inbox_url = AsyncMock(return_value=follower)

        with (
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_queue_repo,
            ),
            patch(
                "app.federation.delivery.FollowerRepository",
                return_value=mock_follower_repo,
            ),
        ):
            await _check_dead_instance("https://dead.example.com/inbox", mock_session)

        assert follower.status == "unreachable"

    async def test_does_not_flag_non_accepted_follower(self) -> None:
        """Does not change status for followers that are not 'accepted'."""
        from app.federation.delivery import _check_dead_instance

        follower = _make_follower("https://dead.example.com/inbox")
        follower.status = "pending"

        mock_session = AsyncMock()
        mock_queue_repo = AsyncMock()
        mock_queue_repo.has_recent_success = AsyncMock(return_value=False)

        mock_follower_repo = AsyncMock()
        mock_follower_repo.get_by_inbox_url = AsyncMock(return_value=follower)

        with (
            patch(
                "app.federation.delivery.DeliveryQueueRepository",
                return_value=mock_queue_repo,
            ),
            patch(
                "app.federation.delivery.FollowerRepository",
                return_value=mock_follower_repo,
            ),
        ):
            await _check_dead_instance("https://dead.example.com/inbox", mock_session)

        assert follower.status == "pending"
