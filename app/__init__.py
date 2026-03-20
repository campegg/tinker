"""Tinker: a single-user ActivityPub microblog."""

from quart import Quart, g

from app.core.config import load_config
from app.core.database import close_engine, get_session_factory, init_engine
from app.public.routes import public


def create_app() -> Quart:
    """Create and configure the Quart application."""
    app = Quart(__name__, static_folder="../static", static_url_path="/assets")

    config = load_config()
    app.config.from_mapping(config)

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

    @app.after_serving
    async def _shutdown() -> None:
        """Dispose of the database engine on shutdown."""
        await close_engine(engine)

    app.register_blueprint(public)

    return app
