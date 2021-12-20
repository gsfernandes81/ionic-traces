import argparse
import asyncio
import datetime as dt
import random
import re
from asyncio.tasks import ALL_COMPLETED
from typing import List, Union

import discord as d
import sqlalchemy as sql
import uvloop
from arrow import Arrow
from dmux import DMux
from pytz import utc
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.expression import delete, select
from timefhuman import timefhuman

from . import cfg
from .cfg import REGISTRATION_TIMEOUT
from .schemas import Base, User

db_engine = create_async_engine(cfg.db_url_async)
db_session = sessionmaker(db_engine, **cfg.db_session_kwargs)


MESSAGE_DELETE_REACTION = "❌"
# Regex discord elements
rgx_d_elems = re.compile("<(@!|#)[0-9]{18}>|<a{0,1}:[a-zA-Z0-9_.]{2,32}:[0-9]{18}>")
# Regex datetime markers
rgx_dt_markers = re.compile("<[^>][^>]+>")


def main():
    dmux = DMux()
    for server in cfg.server_list:
        ionic_server = IonicTraces(*server)
        dmux.register(ionic_server)

    try:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        dmux.run(cfg.discord_token)
    except asyncio.exceptions.CancelledError:
        # Ignore cancellation errors thrown on SIGTERM
        pass


class IonicTraces(DMux):
    def __init__(self, server_id: int, reg_channel_id: Union[int, None] = None):
        super().__init__()
        # The id of the server this instance is handling
        self.server_id = int(server_id)
        # The registration channel id for the server if one is provided
        self.reg_channel_id = (
            int(reg_channel_id) if reg_channel_id is not None else None
        )

    async def on_connect(self):
        try:
            server_name = (await self.client.fetch_guild(self.server_id)).name
        except d.errors.Forbidden:
            # The bot is not authorised to access this server
            print(
                "Ionic Trace connection failed for server id {}".format(self.server_id)
            )
        else:
            print(
                "Ionic Traces connected for server: {} id: {}".format(
                    server_name, self.server_id
                )
            )
            async with db_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

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
                message.guild.id == self.server_id
                and message.author != self.client.user
                and not message.author.bot
            ):
                # Only pass messages through if they're from the mbd server
                await asyncio.wait(
                    [
                        super().on_message(message),
                        self.registration_handler(message),
                        self.deregister_handler(message),
                        self.conversion_handler(message),
                    ],
                    return_when=ALL_COMPLETED,
                )

    async def on_reaction_add(self, reaction: d.Reaction, user: d.User):
        # Do not react to servers we are not supposed to
        if reaction.message.guild.id != self.server_id:
            return

        # Do not respond to reactions on messages not sent by self
        if reaction.message.author.id != self.client.user.id:
            return

        # Do not respond to the specific reactions added by self
        # We can still respond to reactions that match ours,
        # but are added by others
        if user.id == self.client.user.id:
            return

        # Do not respond to emoji we don't care about
        if reaction.emoji != MESSAGE_DELETE_REACTION:
            return

        # If message_reacted to isn't sent by self,
        # ignore it
        message_reacted_to = reaction.message
        if message_reacted_to.author != self.client.user:
            return

        # If reaction is by user that did not trigger its creation
        # ignore it
        channel = reaction.message.channel
        message_replied_to = await channel.fetch_message(
            message_reacted_to.reference.message_id
        )
        time_author = message_replied_to.author
        if time_author.id != user.id:
            return

        # Delete the message if all checks have been passed
        await message_reacted_to.delete()

    async def conversion_handler(self, message: d.Message):
        # Pull properties we want from the message
        user_id = message.author.id
        content = message.content

        # Remove emoji, animated emoji, mentions, channels etc
        # from discord text
        content = rgx_d_elems.sub("", content)

        # Find time tokens
        time_list = rgx_dt_markers.findall(content)
        # Remove the angle brackets
        time_list = [time[1:-1] for time in time_list]
        # Ignore links
        time_list = [time for time in time_list if not time.startswith("http")]
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
        user = await self._get_user_by_id(user_id)

        # If we can't find the user in the db, mention that they can register
        # or if their timezone record is empty
        is_user_not_registered: bool = user is None or user.tz == ""
        if is_user_not_registered:
            response_msg = await message.reply(
                "You haven't registered with me yet or registration has failed\n"
                + "Sending you a registration link in a dm..."
            )
            await response_msg.add_reaction(MESSAGE_DELETE_REACTION)
            await self.register_user(message)
            while True:
                await asyncio.sleep(10)
                user: User = await self._get_user_by_id(user_id)
                if user is None or user.tz == "":
                    continue
                if dt.datetime.now(tz=utc) - user.update_dt > REGISTRATION_TIMEOUT:
                    break
                reply = await self._reply_from_user_and_times(user, time_list)
                try:
                    await response_msg.edit(content=reply)
                except d.errors.NotFound:
                    # Ignore this error: (The message must have been deleted)
                    pass
                break
        else:
            # Use the time list and the user object to create a reply
            reply = await self._reply_from_user_and_times(user, time_list)
            response_msg = await message.reply(reply)
            await response_msg.add_reaction(MESSAGE_DELETE_REACTION)

    async def registration_handler(self, message: d.Message):
        if message.content == "?time" and (
            message.channel.id == self.reg_channel_id or self.reg_channel_id is None
        ):
            await self.register_user(message)
            await message.reply("Check your direct messages for a registration link")

    async def register_user(self, message: d.Message):
        user_id = message.author.id
        link_id = random.randrange(1000000, 9999999, 1)

        # Add the link_id to the db
        async with db_session() as session:
            async with session.begin():
                instance = await session.get(User, int(user_id))
                if instance is None:
                    # If the user hasn't registered yet, create a row for them
                    instance = User(int(user_id), "")
                else:
                    # If they have, make sure to update their datetime to allow
                    # them to register
                    instance.update_dt = dt.datetime.now(tz=utc)
                instance.update_id = link_id
                session.add(instance)

        await message.author.send(
            "Visit this link to register your timezone: \n\n<{}/register/{}>\n\n".format(
                cfg.app_url, link_id
            )
            + "This will collect and store your discord id and your timezone.\n"
            + "Both of these are only used to understand what time you mean when you use the bot. "
            + "This data is stored securely and not processed in any way and can be deleted with "
            + "`?time-deregister` and you can reregister with `?time` in the {} channel".format(
                (await self.client.fetch_channel(message.channel.id)).name
            )
        )

    async def deregister_handler(self, message: d.Message):
        if not (
            message.content == "?time-deregister"
            and message.channel.id == self.reg_channel_id
        ):
            return
        # Find the user in the db
        async with db_session() as session:
            async with session.begin():
                # Delete the user's row
                await session.execute(delete(User).where(User.id == message.author.id))

        await message.reply("You have successfully deregistered")

    @staticmethod
    async def _get_user_by_id(id: int) -> User:
        """Returns the user or None if they aren't found in the db"""
        async with db_session() as session:
            async with session.begin():
                user = (
                    await session.execute(select(User).where(User.id == id))
                ).fetchone()
        return user if user is None else user[0]

    async def _reply_from_user_and_times(self, user: User, time_list: List) -> str:
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
        # Create reply text
        reply = ":F>, <t:".join([str(time) for time in unix_time_list])
        reply = "<t:" + reply + ":F>"
        reply = "That's " + reply + " auto-converted to local time."
        return reply


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
