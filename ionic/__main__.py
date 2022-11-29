import argparse
import asyncio
import datetime as dt
import random
import regex as re
from typing import List, Union

import dateparser
import hikari as h
import lightbulb as lb
import sqlalchemy as sql
import uvloop
from arrow import Arrow
from pytz import utc
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.expression import delete, select

from . import cfg
from .cfg import REGISTRATION_TIMEOUT
from .schemas import Base, User

db_engine = create_async_engine(cfg.db_url_async, connect_args=cfg.db_connect_args)
db_session = sessionmaker(db_engine, **cfg.db_session_kwargs)


MESSAGE_DELETE_REACTION = "❌"
MESSAGE_REFRESH_REACTION = "🔄"
EMOJI_GUILD = 920027638179966996
SWEET_BUSINESS = 1047050852994662400
TELESTO = 1047086753271533608
HIO_UID = 803658060849217556
BRYCE_UID = 204985399926456320

# Regex discord elements
rgx_d_elems = re.compile("<(@!|#)[0-9]{18}>|<a{0,1}:[a-zA-Z0-9_.]{2,32}:[0-9]{18}>")
# Regex datetime markers
rgx_dt_markers = re.compile("<[^>][^>]+>")


class Bot(lb.BotApp):
    async def fetch_channel(self, channel: int):
        return self.cache.get_guild_channel(channel) or await self.rest.fetch_channel(
            channel
        )

    async def fetch_guild(self, guild: int):
        return self.cache.get_guild(guild) or await self.rest.fetch_guild(guild)

    async def fetch_message(
        self, channel: h.SnowflakeishOr[h.TextableChannel], message: int
    ):
        if isinstance(channel, h.Snowflake) or isinstance(channel, int):
            channel = await self.fetch_channel(channel)

        return self.cache.get_message(message) or await self.rest.fetch_message(
            channel, message
        )

    async def fetch_emoji(self, guild_id, emoji_id):
        return bot.cache.get_emoji(emoji_id) or await bot.rest.fetch_emoji(
            guild_id, emoji_id
        )

    def react_to_guild_messages(
        self,
        trigger_regex: re.Pattern,
        reaction: Union[str, h.Emoji],
        allowed_servers: List[int] = [],
        allowed_uids: List[int] = [],
    ):
        async def reaction_handler(event: h.GuildMessageCreateEvent):
            user_id: int = event.author.id
            guild_id: int = event.guild_id
            msg: h.Message = event.message
            msg_text: str = msg.content

            # Ignore empty messages
            if not msg_text:
                return

            if allowed_uids and user_id not in allowed_uids:
                # Ignore the event if the user's id is not in allowed_users
                # Do not ignore if allowed_users is None since that indicates
                # this is enabled for all users
                return

            if allowed_servers and guild_id not in allowed_servers:
                # Ignore the event if the guild id is not in allowed_servers
                # Do not ignore if allowed_servers is None since that indicates
                # this is enabled for all guilds
                return

            if trigger_regex.search(msg_text):
                # Search the message text with regex,
                # if there is a match, react with the specified emoji
                await msg.add_reaction(reaction)

        return bot.listen()(reaction_handler)

    def react_to_guild_reactions(
        self,
        trigger_regex: re.Pattern,
        allowed_servers: List[int] = [],
        allowed_uids: List[int] = [],
    ):
        async def reaction_handler(event: h.GuildReactionAddEvent):
            bot: Bot = event.app
            user_id: int = event.user_id
            channel_id: int = event.channel_id
            guild_id: int = event.guild_id
            emoji_name: str = event.emoji_name
            emoji_id: str = event.emoji_id

            if allowed_uids and user_id not in allowed_uids:
                # Ignore the event if the user's id is not in allowed_users
                # Do not ignore if allowed_users is None since that indicates
                # this is enabled for all users
                return

            if allowed_servers and guild_id not in allowed_servers:
                # Ignore the event if the guild id is not in allowed_servers
                # Do not ignore if allowed_servers is None since that indicates
                # this is enabled for all guilds
                return

            if trigger_regex.search(emoji_name):
                # Search emoji's name with regex,
                # if there is a match, react with the same emoji
                msg = await bot.fetch_message(channel_id, event.message_id)
                try:
                    reaction = await bot.fetch_emoji(guild_id, emoji_id)
                except TypeError:
                    # bot.fetch_emoji throws a TypeError for unicode emoji
                    # since emoji_id is None for these. emoji_name will have the
                    # emoji in this case
                    reaction = emoji_name
                await msg.add_reaction(reaction)

        return bot.listen()(reaction_handler)


bot = Bot(
    cfg.discord_token,
    intents=(
        h.Intents.ALL_UNPRIVILEGED
        | h.Intents.GUILD_MEMBERS
        | h.Intents.MESSAGE_CONTENT
        | h.Intents.ALL_MESSAGE_REACTIONS
    ),
)


async def _time_list_from_string(text: str) -> List[dt.datetime]:
    # Remove emoji, animated emoji, mentions, channels etc
    # from discord text
    text = rgx_d_elems.sub("", text)

    # Find time tokens
    time_list = rgx_dt_markers.findall(text)
    # Remove the angle brackets
    time_list = [time[1:-1] for time in time_list]
    # Ignore links
    time_list = [time for time in time_list if not time.startswith("http")]
    # Parse the human readable time to datetime format
    time_list = [
        dateparser.parse(
            time,
            languages=["en"],
            settings={"PREFER_DATES_FROM": "future"},
        )
        for time in time_list
    ]
    # Filter out items we don't understand
    time_list = [time for time in time_list if time != None]
    # Filter out items in an incorrect format
    time_list = [time for time in time_list if isinstance(time, dt.datetime)]
    return time_list


async def _get_user_by_id(id: int) -> Union[User, None]:
    """Returns the user or None if they aren't found in the db"""
    async with db_session() as session:
        async with session.begin():
            user = (await session.execute(select(User).where(User.id == id))).fetchone()
    return user if user is None else user[0]


async def _convert_time_list_fm_user(user: User, time_list: List) -> List[str]:
    # Get the user's TimeZone
    tz = str(user.tz)

    # Account for time zones
    time_list = [Arrow.fromdatetime(time, tz) for time in time_list]
    # Convert to UTC
    utc_time_list = [time.to("UTC") for time in time_list]
    # Convert to unix time
    unix_time_list = [
        int((time - Arrow(1970, 1, 1)).total_seconds()) for time in utc_time_list
    ]
    discord_time_list = ["<t:" + str(time) + ":t>" for time in unix_time_list]
    return discord_time_list


async def _reply_from_user_and_times(user: User, time_list: List) -> str:
    time_list = await _convert_time_list_fm_user(user, time_list)
    # Create reply text
    reply = ", ".join(time_list)
    reply = "That's " + reply + " auto-converted to local time."
    return reply


async def register_user(message: h.Message):
    user_id = message.author.id

    # Add the link_id to the db
    async with db_session() as session:
        # Generate a new link_id / update_id
        # Ensure that this does not clash with any currently
        # valid link_id
        async with session.begin():
            users = (
                await session.execute(
                    select(User.update_dt, User.update_id).where(
                        dt.datetime.now() - User.update_dt <= REGISTRATION_TIMEOUT
                    )
                )
            ).fetchall()
            users = [] if users is None else users
            used_link_ids = set([user.update_id for user in users])
        while True:
            link_id = random.randrange(1000000, 9999999, 1)
            if link_id not in used_link_ids:
                break

        # Add or prepare to update the user's records
        async with session.begin():
            instance = await session.get(User, int(user_id))
            if instance is None:
                # If the user hasn't registered yet, create a row for them
                instance = User(int(user_id), "")
            else:
                # If they have, make sure to update their datetime to allow
                # them to register
                instance.update_dt = dt.datetime.now()
            instance.update_id = link_id
            session.add(instance)

    await message.author.send(
        "Visit this link to register your timezone: \n\n<{}/register/{}>\n\n".format(
            cfg.app_url, link_id
        )
        + "This will collect and store your discord id and your timezone.\n"
        + "Both of these are only used to understand what time you mean when you use the bot. "
        + "This data is stored securely and not processed in any way and can be deleted with "
        + "`/unregister` and you can reregister by typing <1:00 pm> (or any other time) in a ".format(
            (await bot.fetch_channel(message.channel_id)).name
        )
        + "server with the bot."
    )


@bot.command
@lb.command(
    "unregister",
    "Unregister your time data from the bot",
    auto_defer=True,
)
@lb.implements(lb.SlashCommand)
async def deregister_handler(ctx: lb.Context):
    # Find the user in the db
    async with db_session() as session:
        async with session.begin():
            # Delete the user's row
            await session.execute(delete(User).where(User.id == ctx.author.id))

    await ctx.respond("You have successfully deregistered")


@bot.listen()
async def time_message_handler(event: h.MessageCreateEvent):
    if event.author.is_bot or event.author.is_system:
        return
    # Pull properties we want from the message
    user_id = event.author_id
    message = event.message
    content = message.content

    # Return if we receive an empty message
    if not content:
        return

    time_list = await _time_list_from_string(content)

    # If no times are specified/understood, skip the message
    if len(time_list) == 0:
        return

    # Find the user in the db
    user = await _get_user_by_id(user_id)

    # If we can't find the user in the db, mention that they can register
    # or if their timezone record is empty
    is_user_not_registered: bool = user is None or user.tz == ""
    if is_user_not_registered:
        response_msg: h.Message = await message.respond(
            "You haven't registered with me yet\n"
            + "Sending you a registration link in a dm...",
            reply=True,
        )
        # TODO Buttons here
        # await response_msg.add_reaction(MESSAGE_REFRESH_REACTION)
        # await response_msg.add_reaction(MESSAGE_DELETE_REACTION)
        await register_user(message)
        while True:
            await asyncio.sleep(10)
            user: User = await _get_user_by_id(user_id)
            if dt.datetime.now() - user.update_dt > REGISTRATION_TIMEOUT:
                await response_msg.delete()
                break
            elif user is None or user.tz == "":
                continue
            reply = await _reply_from_user_and_times(user, time_list)
            try:
                await response_msg.edit(content=reply)
            except h.NotFoundError:
                # Ignore this error: (The message must have been deleted)
                pass
            break
    else:
        # Use the time list and the user object to create a reply
        reply = await _reply_from_user_and_times(user, time_list)
        response_msg = await message.respond(reply)
        # Replace the below with buttons
        # await response_msg.add_reaction(MESSAGE_REFRESH_REACTION)
        # await response_msg.add_reaction(MESSAGE_DELETE_REACTION)


@bot.listen()
async def pre_start(event: h.StartingEvent):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@bot.listen()
async def on_lb_start(event: lb.LightbulbStartedEvent):
    # Pizza setup
    bot.react_to_guild_messages(
        trigger_regex=re.compile("(pizza(?![_\s\-,:;'\/\\\+]*milk)|🍕)", re.IGNORECASE),
        reaction="🍕",
        allowed_servers=cfg.pizza_servers,
    )
    bot.react_to_guild_reactions(
        trigger_regex=re.compile("(pizza(?![_\s\-,:;'\/\\\+]*milk)|🍕)", re.IGNORECASE),
        allowed_servers=cfg.pizza_servers,
    )

    # Taco setup
    bot.react_to_guild_messages(
        trigger_regex=re.compile("(taco|🌮)", re.IGNORECASE),
        reaction="🌮",
        allowed_servers=cfg.pizza_servers,
    )
    bot.react_to_guild_reactions(
        trigger_regex=re.compile("(taco|🌮)", re.IGNORECASE),
        allowed_servers=cfg.pizza_servers,
    )

    # Telesto reactions for Hio
    bot.react_to_guild_reactions(
        trigger_regex=re.compile(
            "(telesto|reef\s+in\s+ruins|dread\s+from\s+below|long\s+live\s+the\s+queen)",
            flags=re.IGNORECASE,
        ),
        allowed_servers=cfg.pizza_servers,
        allowed_uids=await bot.fetch_owner_ids() + [HIO_UID],
    )
    bot.react_to_guild_messages(
        trigger_regex=re.compile(
            "long\s+live\s+the\s+queen",
            flags=re.IGNORECASE,
        ),
        reaction=await bot.fetch_emoji(EMOJI_GUILD, TELESTO),
        allowed_servers=cfg.pizza_servers,
    )

    # Sweet business reactions for Bryce
    bot.react_to_guild_reactions(
        trigger_regex=re.compile("^sweet[_ ]business$", flags=re.IGNORECASE),
        allowed_servers=cfg.pizza_servers,
        allowed_uids=await bot.fetch_owner_ids() + [BRYCE_UID],
    )


def main():
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    bot.run()


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release", action="store_true", help="Performs release tasks for heroku"
    )
    parser.add_argument(
        "--reset", action="store_true", help="Deletes everything in the persistent db"
    )
    parser = parser.parse_args()

    if parser.release or parser.reset:
        if parser.reset:
            print("Deleting all tables")
            engine = sql.create_engine(cfg.db_url)
            meta = sql.MetaData()
            meta.reflect(bind=engine)
            for tbl in reversed(meta.sorted_tables):
                print("Dropping table", tbl)
                tbl.drop(engine)
            print("Remaining tables: ", len(sql.MetaData().sorted_tables), sep="")
        # Release tasks go here :
        # None as of now

        # If running an already deployed release, start the discord client
    main()
