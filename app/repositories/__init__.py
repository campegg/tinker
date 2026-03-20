"""Data access layer — one repository per model.

All repository classes are re-exported from this package for convenient
imports elsewhere in the application::

    from app.repositories import NoteRepository, FollowerRepository
"""

from app.repositories.base import BaseRepository
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

__all__ = [
    "BaseRepository",
    "DeliveryQueueRepository",
    "FollowerRepository",
    "FollowingRepository",
    "KeypairRepository",
    "LikeRepository",
    "MediaAttachmentRepository",
    "NoteRepository",
    "NotificationRepository",
    "RemoteActorRepository",
    "SettingsRepository",
    "TimelineItemRepository",
]
