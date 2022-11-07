import argparse
import asyncio
import datetime as dt
import logging
import random
import re
import unicodedata
from asyncio.tasks import ALL_COMPLETED
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

db_engine = create_async_engine(cfg.db_url_async)
db_session = sessionmaker(db_engine, **cfg.db_session_kwargs)


MESSAGE_DELETE_REACTION = "❌"
MESSAGE_REFRESH_REACTION = "🔄"

# Regex discord elements
rgx_d_elems = re.compile("<(@!|#)[0-9]{18}>|<a{0,1}:[a-zA-Z0-9_.]{2,32}:[0-9]{18}>")
# Regex datetime markers
rgx_dt_markers = re.compile("<[^>][^>]+>")


class Bot(lb.BotApp):
    async def fetch_channel(self, channel: int):
        return (
            self.cache.get_guild_channel(channel)
            or self.cache.get_dm_channel(channel)
            or await self.rest.fetch_channel(channel)
        )

    async def fetch_guild(self, guild: int):
        return self.cache.get_guild(guild) or await self.rest.fetch_guild(guild)

    async def fetch_message(self, channel: h.TextableChannel, message: int):
        return self.cache.get_message(message) or await self.rest.fetch_message(
            channel, message
        )


bot = Bot(
    cfg.discord_token,
    intents=(
        h.Intents.ALL_UNPRIVILEGED
        | h.Intents.GUILD_MEMBERS
        | h.Intents.MESSAGE_CONTENT
        | h.Intents.ALL_MESSAGE_REACTIONS
    ),
)


def listen_for_reaction(
    reaction: Union[str, int],
    server_list: List[int],
    bot: Bot,
):
    async def reaction_handler(event: Union[h.ReactionAddEvent, h.MessageCreateEvent]):
        bot: Bot = event.app
        channel = await bot.fetch_channel(event.channel_id)

        try:
            assert channel.guild_id in server_list
            if (
                isinstance(event, h.ReactionAddEvent) and event.emoji_name == reaction
            ) or (
                isinstance(event, h.MessageCreateEvent)
                and unicodedata.name(reaction).split(" ")[-1].lower()
                in event.content.lower()
            ):
                msg = await bot.fetch_message(channel, event.message_id)
                await msg.add_reaction(reaction)
        except AttributeError:
            # Ignore if not a guild (channel.guild_id would raise AttributeError)
            pass
        except AssertionError:
            # Ignore if not a pizza enabled guild
            pass

    return bot.listen()(reaction_handler)


listen_for_reaction("🍕", cfg.pizza_servers, bot)
listen_for_reaction("🌮", cfg.taco_servers, bot)


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

    else:
        # If running an already deployed release, start the discord client
        main()
