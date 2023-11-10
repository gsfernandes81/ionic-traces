import asyncio
import datetime as dt

import jinja2
import quart
from quart import jsonify
from hypercorn.asyncio import serve
from hypercorn.config import Config
from jinja2.loaders import PackageLoader
from jinja2.utils import select_autoescape
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql.expression import select

from . import cfg
from .cfg import REGISTRATION_TIMEOUT
from .schemas import User

config = Config()
config.bind = ["0.0.0.0:{}".format(cfg.port)]
j_env = jinja2.Environment(
    loader=PackageLoader("ionic"), autoescape=select_autoescape(), enable_async=True
)
register_template = j_env.get_template("register.jinja")
success_page = j_env.get_template("success.jinja")
failure_page = j_env.get_template("failure.jinja")
app = quart.Quart("ionic")

db_engine = create_async_engine(cfg.db_url_async, connect_args=cfg.db_connect_args)
db_session = sessionmaker(db_engine, **cfg.db_session_kwargs)


@app.route("/register/<link_id>")
async def send_payload(link_id: int):
    payload = await register_template.render_async(
        base_url=cfg.app_url,
        link_id=link_id,
        stylesheet=quart.url_for("static", filename="styles.css"),
    )
    return payload


@app.route("/static/<path:path>")
async def send_static(path):
    return quart.send_from_directory("static", path)


@app.route("/success")
async def respond_success():
    return await success_page.render_async(
        base_url=cfg.app_url,
        stylesheet=quart.url_for("static", filename="styles.css"),
    )


@app.route("/failure")
async def respond_failure():
    return await failure_page.render_async(
        base_url=cfg.app_url,
        stylesheet=quart.url_for("static", filename="styles.css"),
    )


@app.post("/")
async def receive_timezone():
    timezone = await quart.request.get_json()
    link_id = timezone["link_id"]
    timezone = timezone["tz"]

    async with db_session() as session:
        async with session.begin():
            user = (
                await session.execute(
                    select(User).where(User.update_id == int(link_id))
                )
            ).fetchone()
            if user is None:
                # If there is no such user, then no such user
                # has requested registration
                quart.abort(401)
            user = user[0]
            if dt.datetime.now() - user.update_dt > REGISTRATION_TIMEOUT:
                quart.abort(401)
            user.tz = timezone
    return jsonify(success=True)


if __name__ == "__main__":
    asyncio.run(
        serve(
            app,
            config,
        )
    )
