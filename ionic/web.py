import asyncio
import datetime as dt

import jinja2
import quart
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
j_template = j_env.get_template("time.jinja")
app = quart.Quart("ionic")

db_engine = create_async_engine(cfg.db_url_async)
db_session = sessionmaker(db_engine, **cfg.db_session_kwargs)

REGISTRATION_TIMEOUT = dt.timedelta(minutes=30)


@app.route("/<link_id>")
async def send_payload(link_id: int):
    payload = await j_template.render_async(response_url=cfg.app_url, link_id=link_id)
    return payload


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
                return
            user = user[0]
            if dt.datetime.now(tz=utc) - user.update_dt > REGISTRATION_TIMEOUT:
                return "Link timed out"
            user.tz = timezone
    return "Received"


if __name__ == "__main__":
    asyncio.run(
        serve(
            app,
            config,
        )
    )
