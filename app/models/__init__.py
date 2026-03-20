"""SQLAlchemy ORM models for Tinker.

All models are imported here so that Alembic's autogenerate can detect
them when scanning ``Base.metadata``. Import models from this package
rather than from individual modules.
"""

from app.models.base import Base, UUIDModel
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

__all__ = [
    "Base",
    "DeliveryQueue",
    "Follower",
    "Following",
    "Keypair",
    "Like",
    "MediaAttachment",
    "Note",
    "Notification",
    "RemoteActor",
    "Settings",
    "TimelineItem",
    "UUIDModel",
]
