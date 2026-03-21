"""Tinker: a single-user ActivityPub microblog."""

from quart import Quart, g

from app.core.config import load_config
from app.core.database import close_engine, get_session_factory, init_engine
from app.public.routes import public


def create_app() -> Quart:
    """Create and configure the Quart application.

    Loads configuration from environment variables, initialises the async
    database engine and session factory, wires up request-scoped session
    lifecycle hooks, registers all blueprints, and schedules first-run
    admin password seeding via the ``before_serving`` hook.

    Returns:
        A configured :class:`~quart.Quart` application instance.
    """
    app = Quart(__name__, static_folder="../static", static_url_path="/assets")

    config = load_config()
    app.config.from_mapping(config)

    # Session cookie security — values must be set as native Python types, not
    # strings, so they are applied after from_mapping().
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

    engine = init_engine(app.config["TINKER_DB_PATH"])
    session_factory = get_session_factory(engine)
    app.config["DB_ENGINE"] = engine
    app.config["DB_SESSION_FACTORY"] = session_factory

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

    @app.after_serving
    async def _shutdown() -> None:
        """Dispose of the database engine on shutdown."""
        await close_engine(engine)

    app.register_blueprint(public)

    from app.admin.auth import auth
    from app.admin.routes import admin as admin_bp

    app.register_blueprint(auth)
    app.register_blueprint(admin_bp)

    return app
