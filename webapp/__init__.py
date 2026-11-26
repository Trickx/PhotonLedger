import os

from flask import Flask
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData, event
from sqlalchemy.engine import Engine

from webapp.config import Config

db = SQLAlchemy(metadata=MetaData(naming_convention={
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}))
migrate = Migrate()


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    # web server and indexer run as separate processes: WAL lets them read/write concurrently
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config.from_prefixed_env()
    data = os.path.abspath(app.config["DATA_PATH"])
    app.config["DATA_PATH"] = data
    os.makedirs(data, exist_ok=True)
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(data, "photonledger.db")

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)

    from webapp import models  # noqa: F401  (register models)
    from webapp.main.routes import main
    from webapp.main import night_view, phd_view, rigs, sad_view  # noqa: F401  (add their routes to the blueprint)
    from webapp.main.filters import register_filters

    app.register_blueprint(main)
    register_filters(app)
    from webapp import auth
    auth.init_app(app)
    return app
