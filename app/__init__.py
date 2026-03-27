"""Tinker: a single-user ActivityPub microblog."""

import asyncio
import contextlib
import logging

from quart import Quart, g

from app.core.config import load_config, make_actor_uri
from app.core.database import close_engine, get_session_factory, init_engine
from app.public.routes import public

logger = logging.getLogger(__name__)


def create_app() -> Quart:
    """Create and configure the Quart application.

    Loads configuration from environment variables, initialises the async
    database engine and session factory, wires up request-scoped session
    lifecycle hooks, registers all blueprints, and schedules first-run
    admin password seeding via the ``before_serving`` hook.

    Returns:
        A configured :class:`~quart.Quart` application instance.
    """
    app = Quart(
        __name__,
        static_folder="../static",
        static_url_path="/assets",
        template_folder="../templates",
    )

    config = load_config()
    app.config.from_mapping(config)

    # Session cookie security — values must be set as native Python types, not
    # strings, so they are applied after from_mapping().
    # Secure is disabled when QUART_DEBUG is set so the dev server (plain HTTP)
    # can set and receive the cookie; in production Caddy always serves HTTPS.
    # NOTE: app.debug is not yet set by the CLI at factory time, so we read
    # the env var directly (load_config() has already called load_dotenv()).
    import os as _os

    _debug = _os.environ.get("QUART_DEBUG", "").lower() in ("1", "true", "yes")
    app.config["SESSION_COOKIE_SECURE"] = not _debug
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

    engine = init_engine(app.config["TINKER_DB_PATH"])
    session_factory = get_session_factory(engine)
    app.config["DB_ENGINE"] = engine
    app.config["DB_SESSION_FACTORY"] = session_factory

    # Semaphore shared by all delivery tasks in this process — limits
    # simultaneous outbound HTTP requests to remote inboxes.
    app.config["DELIVERY_SEMAPHORE"] = asyncio.Semaphore(10)

    # Queue for real-time notification events emitted by inbox processing.
    # The SSE endpoint (WP-16) reads from this queue to push events to the
    # connected admin client.  Unbounded capacity is acceptable for a
    # single-user server; events are also persisted to the DB.
    app.config["NOTIFICATION_QUEUE"] = asyncio.Queue()

    @app.before_request
    async def _open_session() -> None:
        """Create a request-scoped database session."""
        g.db_session = session_factory()

    @app.teardown_appcontext
    async def _close_session(exc: BaseException | None) -> None:
        """Close the request-scoped database session."""
        session = g.pop("db_session", None)
        if session is not None:
            await session.close()

    @app.before_serving
    async def _seed_admin_password() -> None:
        """Hash and store the admin password on first run.

        If ``TINKER_ADMIN_PASSWORD`` is set in the environment and no
        password hash exists in the database yet, hashes the plaintext
        password with argon2 and stores it. Subsequent starts with the
        same env var set are no-ops once the hash is persisted.
        """
        admin_password: str = app.config.get("TINKER_ADMIN_PASSWORD", "")
        if not admin_password:
            return

        from app.admin.auth import hash_password
        from app.services.settings import SettingsService

        # Use the context-manager form so the session is always closed and
        # its underlying connection returned to the pool, even on error.
        async with session_factory() as db_session:
            try:
                settings = SettingsService(db_session)
                existing = await settings.get_admin_password_hash()
                if existing is None:
                    await settings.set_admin_password_hash(hash_password(admin_password))
            except Exception:
                await db_session.rollback()
                raise

    @app.before_serving
    async def _delivery_startup() -> None:
        """Re-dispatch incomplete deliveries and start the retry loop.

        Loads the local RSA private key once, then:

        1. Calls :func:`~app.federation.delivery.startup_recovery` to
           re-dispatch any ``"pending"`` queue entries that survived a
           process restart.
        2. Starts :func:`~app.federation.delivery.retry_loop` as a
           background asyncio task that periodically retries deliveries
           whose exponential-backoff delay has expired.
        """
        from app.federation.delivery import retry_loop, startup_recovery
        from app.services.keypair import KeypairService

        domain: str = app.config["TINKER_DOMAIN"]
        username: str = app.config["TINKER_USERNAME"]
        semaphore: asyncio.Semaphore = app.config["DELIVERY_SEMAPHORE"]

        async with session_factory() as db_session:
            try:
                keypair_svc = KeypairService(db_session)
                private_key_pem = await keypair_svc.get_private_key()
            except Exception:
                logger.exception("Failed to load keypair during delivery startup; skipping")
                return

        key_id = f"{make_actor_uri(domain, username)}#main-key"

        # Expose the private key and key_id in app config so that the inbox
        # endpoint can sign Accept{Follow} deliveries without reloading the
        # keypair on every request.
        app.config["INBOX_PRIVATE_KEY_PEM"] = private_key_pem
        app.config["INBOX_KEY_ID"] = key_id

        await startup_recovery(
            session_factory=session_factory,
            private_key_pem=private_key_pem,
            key_id=key_id,
            semaphore=semaphore,
        )

        loop_task = asyncio.create_task(
            retry_loop(
                session_factory=session_factory,
                private_key_pem=private_key_pem,
                key_id=key_id,
                semaphore=semaphore,
            ),
            name="delivery-retry-loop",
        )
        app.config["DELIVERY_RETRY_LOOP_TASK"] = loop_task

    @app.after_serving
    async def _shutdown() -> None:
        """Cancel the delivery retry loop and dispose of the database engine on shutdown."""
        loop_task: asyncio.Task[None] | None = app.config.get("DELIVERY_RETRY_LOOP_TASK")
        if loop_task is not None and not loop_task.done():
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task

        await close_engine(engine)

    app.register_blueprint(public)

    from app.admin.api import api as api_bp
    from app.admin.auth import auth
    from app.admin.routes import admin as admin_bp
    from app.admin.sse import sse_bp

    app.register_blueprint(auth)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(sse_bp)

    return app
