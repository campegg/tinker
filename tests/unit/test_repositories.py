from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from app.models.boost import Boost
from app.models.delivery_queue import DeliveryQueue
from app.models.follower import Follower
from app.models.following import Following
from app.models.keypair import Keypair
from app.models.like import Like
from app.models.media_attachment import MediaAttachment
from app.models.note import Note
from app.models.notification import Notification
from app.models.remote_actor import RemoteActor
from app.models.settings import Settings
from app.models.timeline_item import TimelineItem
from app.repositories.boost import BoostRepository
from app.repositories.delivery_queue import DeliveryQueueRepository
from app.repositories.follower import FollowerRepository
from app.repositories.following import FollowingRepository
from app.repositories.keypair import KeypairRepository
from app.repositories.like import LikeRepository
from app.repositories.media_attachment import MediaAttachmentRepository
from app.repositories.note import NoteRepository
from app.repositories.notification import NotificationRepository
from app.repositories.remote_actor import RemoteActorRepository
from app.repositories.settings import SettingsRepository
from app.repositories.timeline_item import TimelineItemRepository

# ---------------------------------------------------------------------------
# BaseRepository behaviour (tested through NoteRepository as a concrete impl)
# ---------------------------------------------------------------------------


class TestBaseRepositoryViaNoteRepository:
    def test_constructor_accepts_session(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is Note

    async def test_get_by_id_delegates_to_session_get(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        entity_id = uuid.uuid4()
        mock_note = MagicMock(spec=Note)
        mock_session.get.return_value = mock_note

        result = await repo.get_by_id(entity_id)

        mock_session.get.assert_awaited_once_with(Note, entity_id)
        assert result is mock_note

    async def test_get_by_id_returns_none_when_not_found(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        mock_session.get.return_value = None

        result = await repo.get_by_id(uuid.uuid4())

        assert result is None

    async def test_get_all_calls_execute_and_returns_scalars(
        self, mock_session: AsyncMock
    ) -> None:
        repo = NoteRepository(mock_session)
        mock_notes = [MagicMock(spec=Note), MagicMock(spec=Note)]
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=mock_notes)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_all()

        mock_session.execute.assert_awaited_once()
        mock_result.scalars.assert_called_once()
        assert result == mock_notes

    async def test_add_calls_add_flush_refresh(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        mock_note = MagicMock(spec=Note)

        result = await repo.add(mock_note)

        mock_session.add.assert_called_once_with(mock_note)
        mock_session.flush.assert_awaited_once()
        mock_session.refresh.assert_awaited_once_with(mock_note)
        assert result is mock_note

    async def test_delete_calls_delete_and_flush(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        mock_note = MagicMock(spec=Note)
        # The conftest sets session.delete as MagicMock, but BaseRepository
        # awaits it, so override with AsyncMock for this test.
        mock_session.delete = AsyncMock()

        await repo.delete(mock_note)

        mock_session.delete.assert_awaited_once_with(mock_note)
        mock_session.flush.assert_awaited_once()

    async def test_commit_delegates_to_session(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)

        await repo.commit()

        mock_session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# NoteRepository
# ---------------------------------------------------------------------------


class TestNoteRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is Note

    async def test_get_by_ap_id_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        mock_note = MagicMock(spec=Note)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_note)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_ap_id("https://example.com/notes/1")

        mock_session.execute.assert_awaited_once()
        assert result is mock_note

    async def test_get_by_ap_id_returns_none_when_not_found(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)

        result = await repo.get_by_ap_id("https://example.com/notes/missing")

        assert result is None

    async def test_get_recent_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        mock_notes = [MagicMock(spec=Note)]
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=mock_notes)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_recent(limit=10, offset=5)

        mock_session.execute.assert_awaited_once()
        assert result == mock_notes

    async def test_get_recent_uses_defaults(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)

        await repo.get_recent()

        mock_session.execute.assert_awaited_once()

    async def test_count_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=42)
        mock_session.execute.return_value = mock_result

        result = await repo.count()

        mock_session.execute.assert_awaited_once()
        assert result == 42

    async def test_count_returns_zero_when_scalar_is_none(self, mock_session: AsyncMock) -> None:
        repo = NoteRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=None)
        mock_session.execute.return_value = mock_result

        result = await repo.count()

        assert result == 0


# ---------------------------------------------------------------------------
# RemoteActorRepository
# ---------------------------------------------------------------------------


class TestRemoteActorRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = RemoteActorRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is RemoteActor

    async def test_get_by_uri_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = RemoteActorRepository(mock_session)
        mock_actor = MagicMock(spec=RemoteActor)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_actor)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_uri("https://remote.example.com/users/alice")

        mock_session.execute.assert_awaited_once()
        assert result is mock_actor

    async def test_get_by_uri_returns_none_when_not_found(self, mock_session: AsyncMock) -> None:
        repo = RemoteActorRepository(mock_session)

        result = await repo.get_by_uri("https://remote.example.com/users/nobody")

        assert result is None

    async def test_get_by_handle_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = RemoteActorRepository(mock_session)
        mock_actor = MagicMock(spec=RemoteActor)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_actor)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_handle("alice@remote.example.com")

        mock_session.execute.assert_awaited_once()
        assert result is mock_actor

    async def test_get_by_handle_returns_none_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = RemoteActorRepository(mock_session)

        result = await repo.get_by_handle("nobody@remote.example.com")

        assert result is None


# ---------------------------------------------------------------------------
# FollowerRepository
# ---------------------------------------------------------------------------


class TestFollowerRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = FollowerRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is Follower

    async def test_get_by_actor_uri_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = FollowerRepository(mock_session)
        mock_follower = MagicMock(spec=Follower)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_follower)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_actor_uri("https://remote.example.com/users/bob")

        mock_session.execute.assert_awaited_once()
        assert result is mock_follower

    async def test_get_by_actor_uri_returns_none_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = FollowerRepository(mock_session)

        result = await repo.get_by_actor_uri("https://remote.example.com/users/nobody")

        assert result is None

    async def test_get_accepted_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = FollowerRepository(mock_session)
        mock_followers = [MagicMock(spec=Follower), MagicMock(spec=Follower)]
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=mock_followers)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_accepted(limit=10, offset=5)

        mock_session.execute.assert_awaited_once()
        assert result == mock_followers

    async def test_get_accepted_uses_defaults(self, mock_session: AsyncMock) -> None:
        repo = FollowerRepository(mock_session)

        await repo.get_accepted()

        mock_session.execute.assert_awaited_once()

    async def test_count_accepted_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = FollowerRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=7)
        mock_session.execute.return_value = mock_result

        result = await repo.count_accepted()

        mock_session.execute.assert_awaited_once()
        assert result == 7

    async def test_count_accepted_returns_zero_when_none(self, mock_session: AsyncMock) -> None:
        repo = FollowerRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=None)
        mock_session.execute.return_value = mock_result

        result = await repo.count_accepted()

        assert result == 0


# ---------------------------------------------------------------------------
# FollowingRepository
# ---------------------------------------------------------------------------


class TestFollowingRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = FollowingRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is Following

    async def test_get_by_actor_uri_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = FollowingRepository(mock_session)
        mock_following = MagicMock(spec=Following)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_following)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_actor_uri("https://remote.example.com/users/carol")

        mock_session.execute.assert_awaited_once()
        assert result is mock_following

    async def test_get_by_actor_uri_returns_none_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = FollowingRepository(mock_session)

        result = await repo.get_by_actor_uri("https://remote.example.com/users/nobody")

        assert result is None

    async def test_get_accepted_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = FollowingRepository(mock_session)
        mock_followings = [MagicMock(spec=Following)]
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=mock_followings)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_accepted(limit=25, offset=0)

        mock_session.execute.assert_awaited_once()
        assert result == mock_followings

    async def test_get_accepted_uses_defaults(self, mock_session: AsyncMock) -> None:
        repo = FollowingRepository(mock_session)

        await repo.get_accepted()

        mock_session.execute.assert_awaited_once()

    async def test_count_accepted_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = FollowingRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=12)
        mock_session.execute.return_value = mock_result

        result = await repo.count_accepted()

        mock_session.execute.assert_awaited_once()
        assert result == 12

    async def test_count_accepted_returns_zero_when_none(self, mock_session: AsyncMock) -> None:
        repo = FollowingRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=None)
        mock_session.execute.return_value = mock_result

        result = await repo.count_accepted()

        assert result == 0


# ---------------------------------------------------------------------------
# TimelineItemRepository
# ---------------------------------------------------------------------------


class TestTimelineItemRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = TimelineItemRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is TimelineItem

    async def test_get_recent_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = TimelineItemRepository(mock_session)
        mock_items = [MagicMock(spec=TimelineItem)]
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=mock_items)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_recent(limit=10)

        mock_session.execute.assert_awaited_once()
        assert result == mock_items

    async def test_get_recent_with_before_id_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = TimelineItemRepository(mock_session)
        cursor_id = uuid.uuid4()

        await repo.get_recent(limit=20, before_id=cursor_id)

        mock_session.execute.assert_awaited_once()

    async def test_get_recent_uses_defaults(self, mock_session: AsyncMock) -> None:
        repo = TimelineItemRepository(mock_session)

        await repo.get_recent()

        mock_session.execute.assert_awaited_once()

    async def test_get_by_object_uri_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = TimelineItemRepository(mock_session)
        mock_item = MagicMock(spec=TimelineItem)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_item)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_object_uri("https://remote.example.com/notes/42")

        mock_session.execute.assert_awaited_once()
        assert result is mock_item

    async def test_get_by_object_uri_returns_none_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = TimelineItemRepository(mock_session)

        result = await repo.get_by_object_uri("https://remote.example.com/notes/missing")

        assert result is None


# ---------------------------------------------------------------------------
# NotificationRepository
# ---------------------------------------------------------------------------


class TestNotificationRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = NotificationRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is Notification

    async def test_get_recent_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = NotificationRepository(mock_session)
        mock_notifications = [MagicMock(spec=Notification)]
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=mock_notifications)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_recent(limit=10, offset=5)

        mock_session.execute.assert_awaited_once()
        assert result == mock_notifications

    async def test_get_recent_uses_defaults(self, mock_session: AsyncMock) -> None:
        repo = NotificationRepository(mock_session)

        await repo.get_recent()

        mock_session.execute.assert_awaited_once()

    async def test_get_unread_count_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = NotificationRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=5)
        mock_session.execute.return_value = mock_result

        result = await repo.get_unread_count()

        mock_session.execute.assert_awaited_once()
        assert result == 5

    async def test_get_unread_count_returns_zero_when_none(self, mock_session: AsyncMock) -> None:
        repo = NotificationRepository(mock_session)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=None)
        mock_session.execute.return_value = mock_result

        result = await repo.get_unread_count()

        assert result == 0

    async def test_mark_all_read_calls_execute_with_update(self, mock_session: AsyncMock) -> None:
        repo = NotificationRepository(mock_session)

        await repo.mark_all_read()

        mock_session.execute.assert_awaited_once()
        mock_session.flush.assert_awaited_once()

    async def test_mark_all_read_executes_before_flush(self, mock_session: AsyncMock) -> None:
        repo = NotificationRepository(mock_session)
        call_order: list[str] = []

        async def _track_execute(*a: object, **kw: object) -> MagicMock:
            call_order.append("execute")
            return MagicMock()

        async def _track_flush(*a: object, **kw: object) -> None:
            call_order.append("flush")

        mock_session.execute.side_effect = _track_execute
        mock_session.flush.side_effect = _track_flush

        await repo.mark_all_read()

        assert call_order == ["execute", "flush"]


# ---------------------------------------------------------------------------
# DeliveryQueueRepository
# ---------------------------------------------------------------------------


class TestDeliveryQueueRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = DeliveryQueueRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is DeliveryQueue

    async def test_get_pending_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = DeliveryQueueRepository(mock_session)
        mock_items = [MagicMock(spec=DeliveryQueue)]
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=mock_items)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_pending()

        mock_session.execute.assert_awaited_once()
        assert result == mock_items

    async def test_get_pending_returns_empty_when_none_pending(
        self, mock_session: AsyncMock
    ) -> None:
        repo = DeliveryQueueRepository(mock_session)

        result = await repo.get_pending()

        mock_session.execute.assert_awaited_once()
        assert result == []

    async def test_get_retryable_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = DeliveryQueueRepository(mock_session)
        mock_items = [MagicMock(spec=DeliveryQueue)]
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=mock_items)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_retryable()

        mock_session.execute.assert_awaited_once()
        assert result == mock_items

    async def test_get_retryable_returns_empty_when_nothing_retryable(
        self, mock_session: AsyncMock
    ) -> None:
        repo = DeliveryQueueRepository(mock_session)

        result = await repo.get_retryable()

        mock_session.execute.assert_awaited_once()
        assert result == []


# ---------------------------------------------------------------------------
# SettingsRepository
# ---------------------------------------------------------------------------


class TestSettingsRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = SettingsRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is Settings

    async def test_get_by_key_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = SettingsRepository(mock_session)
        mock_setting = MagicMock(spec=Settings)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_setting)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_key("display_name")

        mock_session.execute.assert_awaited_once()
        assert result is mock_setting

    async def test_get_by_key_returns_none_when_not_found(self, mock_session: AsyncMock) -> None:
        repo = SettingsRepository(mock_session)

        result = await repo.get_by_key("nonexistent_key")

        assert result is None

    async def test_set_value_updates_existing_setting(self, mock_session: AsyncMock) -> None:
        repo = SettingsRepository(mock_session)
        mock_setting = MagicMock(spec=Settings)
        mock_setting.value = "old_value"

        # Make get_by_key return the existing setting
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_setting)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.set_value("display_name", "New Name")

        assert mock_setting.value == "New Name"
        mock_session.flush.assert_awaited()
        mock_session.refresh.assert_awaited_with(mock_setting)
        assert result is mock_setting

    async def test_set_value_creates_new_setting_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = SettingsRepository(mock_session)

        # Default mock_session returns None for scalars().first() — setting not found

        result = await repo.set_value("new_key", "new_value")

        # set_value should call add() which calls session.add, flush, refresh
        mock_session.add.assert_called_once()
        added_entity = mock_session.add.call_args[0][0]
        assert isinstance(added_entity, Settings)
        assert added_entity.key == "new_key"
        assert added_entity.value == "new_value"
        mock_session.flush.assert_awaited()
        assert result is not None

    async def test_set_value_with_none_clears_existing(self, mock_session: AsyncMock) -> None:
        repo = SettingsRepository(mock_session)
        mock_setting = MagicMock(spec=Settings)
        mock_setting.value = "old_value"

        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_setting)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.set_value("display_name", None)

        assert mock_setting.value is None
        assert result is mock_setting

    async def test_set_value_creates_with_none_value(self, mock_session: AsyncMock) -> None:
        repo = SettingsRepository(mock_session)

        # Default mock returns None for scalars().first() — not found

        await repo.set_value("new_key", None)

        mock_session.add.assert_called_once()
        added_entity = mock_session.add.call_args[0][0]
        assert isinstance(added_entity, Settings)
        assert added_entity.key == "new_key"
        assert added_entity.value is None


# ---------------------------------------------------------------------------
# MediaAttachmentRepository
# ---------------------------------------------------------------------------


class TestMediaAttachmentRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = MediaAttachmentRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is MediaAttachment

    async def test_get_by_note_id_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = MediaAttachmentRepository(mock_session)
        note_id = uuid.uuid4()
        mock_attachments = [MagicMock(spec=MediaAttachment)]
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=mock_attachments)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_note_id(note_id)

        mock_session.execute.assert_awaited_once()
        assert result == mock_attachments

    async def test_get_by_note_id_returns_empty_when_none(self, mock_session: AsyncMock) -> None:
        repo = MediaAttachmentRepository(mock_session)

        result = await repo.get_by_note_id(uuid.uuid4())

        mock_session.execute.assert_awaited_once()
        assert result == []


# ---------------------------------------------------------------------------
# LikeRepository
# ---------------------------------------------------------------------------


class TestLikeRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = LikeRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is Like

    async def test_get_by_note_uri_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = LikeRepository(mock_session)
        mock_like = MagicMock(spec=Like)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_like)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_note_uri("https://example.com/notes/1")

        mock_session.execute.assert_awaited_once()
        assert result is mock_like

    async def test_get_by_note_uri_returns_none_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = LikeRepository(mock_session)

        result = await repo.get_by_note_uri("https://example.com/notes/missing")

        assert result is None

    async def test_get_by_activity_uri_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = LikeRepository(mock_session)
        mock_like = MagicMock(spec=Like)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_like)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_activity_uri("https://remote.example.com/activities/like/1")

        mock_session.execute.assert_awaited_once()
        assert result is mock_like

    async def test_get_by_activity_uri_returns_none_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = LikeRepository(mock_session)

        result = await repo.get_by_activity_uri(
            "https://remote.example.com/activities/like/missing"
        )

        assert result is None

    async def test_get_by_note_and_actor_returns_match(self, mock_session: AsyncMock) -> None:
        repo = LikeRepository(mock_session)
        mock_like = MagicMock(spec=Like)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_like)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_note_and_actor(
            "https://remote.example.com/notes/1",
            "https://example.com/users/alice",
        )

        mock_session.execute.assert_awaited_once()
        assert result is mock_like

    async def test_get_by_note_and_actor_returns_none_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = LikeRepository(mock_session)

        result = await repo.get_by_note_and_actor(
            "https://remote.example.com/notes/missing",
            "https://example.com/users/alice",
        )

        assert result is None


# ---------------------------------------------------------------------------
# BoostRepository
# ---------------------------------------------------------------------------


class TestBoostRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = BoostRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is Boost

    async def test_get_by_note_and_actor_returns_match(self, mock_session: AsyncMock) -> None:
        repo = BoostRepository(mock_session)
        mock_boost = MagicMock(spec=Boost)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_boost)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_note_and_actor(
            "https://remote.example.com/notes/1",
            "https://example.com/users/alice",
        )

        mock_session.execute.assert_awaited_once()
        assert result is mock_boost

    async def test_get_by_note_and_actor_returns_none_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = BoostRepository(mock_session)

        result = await repo.get_by_note_and_actor(
            "https://remote.example.com/notes/missing",
            "https://example.com/users/alice",
        )

        assert result is None

    async def test_get_by_activity_uri_returns_match(self, mock_session: AsyncMock) -> None:
        repo = BoostRepository(mock_session)
        mock_boost = MagicMock(spec=Boost)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_boost)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_activity_uri("https://example.com/users/alice#boost-abc123")

        mock_session.execute.assert_awaited_once()
        assert result is mock_boost

    async def test_get_by_activity_uri_returns_none_when_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        repo = BoostRepository(mock_session)

        result = await repo.get_by_activity_uri("https://example.com/users/alice#boost-missing")

        assert result is None


# ---------------------------------------------------------------------------
# KeypairRepository
# ---------------------------------------------------------------------------


class TestKeypairRepository:
    def test_constructor(self, mock_session: AsyncMock) -> None:
        repo = KeypairRepository(mock_session)
        assert repo._session is mock_session
        assert repo._model_class is Keypair

    async def test_get_active_calls_execute(self, mock_session: AsyncMock) -> None:
        repo = KeypairRepository(mock_session)
        mock_keypair = MagicMock(spec=Keypair)
        mock_scalars = MagicMock()
        mock_scalars.first = MagicMock(return_value=mock_keypair)
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute.return_value = mock_result

        result = await repo.get_active()

        mock_session.execute.assert_awaited_once()
        assert result is mock_keypair

    async def test_get_active_returns_none_when_no_keypairs(self, mock_session: AsyncMock) -> None:
        repo = KeypairRepository(mock_session)

        result = await repo.get_active()

        mock_session.execute.assert_awaited_once()
        assert result is None
