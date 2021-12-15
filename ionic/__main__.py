import random
import re

from arrow import Arrow
import discord as d
import jinja2
import quart
from timefhuman import timefhuman
from dmux import DMux
from hypercorn.asyncio import serve
from hypercorn.config import Config
from jinja2.loaders import PackageLoader
from jinja2.utils import select_autoescape
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql.expression import select, delete
from sqlalchemy.sql.schema import Column
from sqlalchemy.sql.sqltypes import BigInteger, String

from . import cfg

Base = declarative_base()
db_engine = create_async_engine(cfg.db_url_async)
db_session = sessionmaker(db_engine, **cfg.db_session_kwargs)


config = Config()
config.bind = ["0.0.0.0:{}".format(cfg.port)]
j_env = jinja2.Environment(
    loader=PackageLoader("ionic"), autoescape=select_autoescape()
)
j_template = j_env.get_template("time.jinja")
app = quart.Quart("ionic")

open_registration_list = {}


class User(Base):
    __tablename__ = "mbd_user"
    __mapper_args__ = {"eager_defaults": True}
    id = Column("id", BigInteger, primary_key=True)
    tz = Column("tz", String)

    def __init__(self, id, tz):
        self.id = id
        self.tz = tz


@app.route("/<link_id>")
async def send_payload(link_id: int):
    payload = j_template.render(response_url=cfg.app_url, link_id=link_id)
    return payload


@app.post("/")
async def receive_timezone():
    timezone = await quart.request.get_json()
    link_id = timezone["link_id"]
    timezone = timezone["tz"]
    try:
        user_id = open_registration_list.pop(link_id)
    except KeyError:
        return "No Such Registration in Progress"

    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_session() as session:
        async with session.begin():
            instance = await session.get(User, int(user_id))
            if instance is None:
                instance = User(int(user_id), str(timezone))
                session.add(instance)
            else:
                instance.tz = timezone

    return "Received"


class IonicTraces(DMux):
    def __init__(self):
        super().__init__()

    async def on_connect(self):
        print("Ionic Traces Connected")
        self.bot_channel_id: d.TextChannel = cfg.bot_channel_id
        async with db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await serve(app, config)

    async def on_message(self, message: d.Message):
        try:
            # Try access message.guild.id
            # This will fail with AttributeError if
            # the message isn't in a guild at all
            message.guild.id
        except AttributeError:
            pass
        else:
            if (
                message.guild.id == cfg.mbd_server_id
                and message.author != self.client.user
            ):
                # Only pass messages through if they're from the mbd server
                await super().on_message(message)
                await self.registration_handler(message)
                await self.deregister_handler(message)
                await self.conversion_handler(message)

    async def conversion_handler(self, message: d.Message):
        # Ignore messages not in a LFG channel
        channel_and_parents_names = str()
        channel = message.channel
        while True:
            channel_and_parents_names += channel.name + " "
            try:
                channel = channel.parent
            except AttributeError:
                break
        if "lfg" not in channel_and_parents_names:
            return

        # Pull properties we want from the message
        user_id = message.author.id
        content = message.content

        # Find time tokens
        time_list = re.findall("<[^>]+>", content)
        # Remove the angle brackets
        time_list = [time[1:-1] for time in time_list]
        # Timefhuman always seems to throw a value error. Ignore these for now
        try:
            # Parse the human readable time to datetime format
            time_list = [timefhuman(time) for time in time_list]
        except ValueError:
            pass
        # Filter out items we don't understand
        time_list = [time for time in time_list if time != []]

        # If no times are specified/understood, skip the message
        if len(time_list) == 0:
            return

        # Find the user in the db
        async with db_session() as session:
            async with session.begin():
                user = (
                    await session.execute(select(User).where(User.id == user_id))
                ).fetchone()

        # If we can't find the user in the db, mention that they can register
        if user is None:
            await message.reply(
                "You haven't registered with me yet or registration has failed\n"
                + "Register by typing `?time` in the <#{}> channel".format(
                    self.bot_channel_id
                )
            )
            return

        # Get the user's TimeZone
        tz = str(user[0].tz)

        # Account for time zones
        time_list = [Arrow.fromdatetime(time, tz) for time in time_list]
        # Convert to UTC
        utc_time_list = [time.to("UTC") for time in time_list]
        # Convert to unix time
        unix_time_list = [
            int((time - Arrow(1970, 1, 1)).total_seconds()) for time in utc_time_list
        ]
        # Create reply text
        reply = ":F>, <t:".join([str(time) for time in unix_time_list])
        reply = "<t:" + reply + ":F>"
        reply = "That's " + reply + " auto-converted to local time."
        await message.reply(reply)

    async def registration_handler(self, message: d.Message):
        if not (
            message.content == "?time" and message.channel.id == self.bot_channel_id
        ):
            return
        user_id = message.author.id
        link_id = str(random.randrange(1000000, 9999999, 1))
        open_registration_list[link_id] = user_id

        await message.author.send(
            "Visit this link to register your timezone: \n\n<{}{}>\n\n".format(
                cfg.app_url, link_id
            )
            + "This will collect and store your discord id and your timezone.\n"
            + "Both of these are only used to understand what time you mean when you use the bot. "
            + "This data is stored securely and not processed in any way and can be deleted with "
            + "`?time-deregister`"
        )

    async def deregister_handler(self, message: d.Message):
        if not (
            message.content == "?time-deregister"
            and message.channel.id == self.bot_channel_id
        ):
            return
        # Find the user in the db
        async with db_session() as session:
            async with session.begin():
                # Delete the user's row
                await session.execute(delete(User).where(User.id == message.author.id))


if __name__ == "__main__":
    dmux = DMux()
    ionic = IonicTraces()
    dmux.register(ionic)

    dmux.run(cfg.discord_token)
