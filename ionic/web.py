import asyncio
import datetime as dt

import jinja2
import quart
from quart import jsonify
from hypercorn.asyncio import serve
from hypercorn.config import Config
from jinja2.loaders import PackageLoader
from jinja2.utils import select_autoescape
from pytz import utc
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql.expression import select

from . import cfg
from .schemas import User

config = Config()
config.bind = ["0.0.0.0:{}".format(cfg.port)]
j_env = jinja2.Environment(
    loader=PackageLoader("ionic"), autoescape=select_autoescape(), enable_async=True
)
register_template = j_env.get_template("time.jinja")
success_page = j_env.get_template("success.jinja")
failure_page = j_env.get_template("failure.jinja")
app = quart.Quart("ionic")

db_engine = create_async_engine(cfg.db_url_async)
db_session = sessionmaker(db_engine, **cfg.db_session_kwargs)

REGISTRATION_TIMEOUT = dt.timedelta(minutes=30)


@app.route("/register/<link_id>")
async def send_payload(link_id: int):
    payload = await register_template.render_async(
        response_url=cfg.app_url, link_id=link_id
    )
    return payload


@app.route("/success")
async def respond_success():
    return await success_page.render_async()


@app.route("/failure")
async def respond_failure():
    return await failure_page.render_async()


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
            if dt.datetime.now(tz=utc) - user.update_dt > REGISTRATION_TIMEOUT:
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
