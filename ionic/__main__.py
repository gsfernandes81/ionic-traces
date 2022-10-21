import argparse
import asyncio
import datetime as dt
import random
import re
from asyncio.tasks import ALL_COMPLETED
from typing import List, Union

import dateparser
import discord as d
import sqlalchemy as sql
import uvloop
from arrow import Arrow
from dmux import DMux
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


def main():

    intents = d.Intents.default()
    intents.members = True
    intents.message_content = True
    intents.reactions = True

    dmux = DMux(intents=intents, guild_subscriptions=True)
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
    def __init__(
        self,
        server_id: int,
        reg_channel_id: Union[int, None] = None,
        extra_features: Union[bool, None] = False,
    ):
        super().__init__()
        # The id of the server this instance is handling
        self.server_id = int(server_id)
        # The registration channel id for the server if one is provided
        self.reg_channel_id = (
            int(reg_channel_id) if reg_channel_id is not None else None
        )
        self.extra_features = extra_features

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
                        self.pizza_message_handler(message),
                    ],
                    return_when=ALL_COMPLETED,
                )

    async def on_raw_reaction_add(self, payload: d.RawReactionActionEvent):
        try:
            channel = await self.client.fetch_channel(payload.channel_id)
            message_by_bot: d.Message = await channel.fetch_message(payload.message_id)
            reaction_emoji = payload.emoji.name
            reacting_user = await self.client.fetch_user(payload.user_id)
        except d.errors.Forbidden:
            # Ignore forbidden errors for this block of fetches
            # If we get a forbidden error here, this means that we don't have permissions
            # to see this channel, its messages and/or its reactions
            return

        # Do not react to servers we are not supposed to
        if message_by_bot.guild.id != self.server_id:
            return

        # Pizza
        if reaction_emoji == "🍕" and reacting_user.id != self.client.user.id:
            await message_by_bot.add_reaction("🍕")

        # Taco
        if reaction_emoji == "🌮" and reacting_user.id != self.client.user.id:
            await message_by_bot.add_reaction("🌮")

        # Do not respond to reactions on messages not sent by self
        if message_by_bot.author.id != self.client.user.id:
            return

        # Do not respond to the specific reactions added by self
        # We can still respond to reactions that match ours,
        # but are added by others
        if reacting_user.id == self.client.user.id:
            return

        # Do not respond to emoji we don't care about
        if not (
            reaction_emoji == MESSAGE_DELETE_REACTION
            or reaction_emoji == MESSAGE_REFRESH_REACTION
        ):
            return

        try:
            # If reaction is by user that did not trigger its creation
            # ignore it
            message_by_user = await channel.fetch_message(
                message_by_bot.reference.message_id
            )
        except d.errors.NotFound:
            # If the message was not found, say so and return
            await message_by_bot.edit(
                "*Message disappeared!*  : O \n" + "*Deleting in 10...*"
            )
            await message_by_bot.delete(delay=10)
            return

        time_author = message_by_user.author
        if time_author.id != reacting_user.id:
            try:
                await message_by_bot.remove_reaction(reaction_emoji, reacting_user)
            except d.errors.Forbidden:
                # If removing reactions is not allowed,
                # ignore this step
                pass
            return

        if reaction_emoji == MESSAGE_DELETE_REACTION:
            # Delete the message if all checks have been passed
            await message_by_bot.delete()
        elif reaction_emoji == MESSAGE_REFRESH_REACTION:
            try:
                await message_by_bot.remove_reaction(reaction_emoji, reacting_user)
            except d.errors.Forbidden:
                # If removing reactions is not allowed,
                # ignore this step
                pass

            time_list = await self._time_list_from_string(message_by_user.content)
            if len(time_list) == 0:
                await message_by_bot.edit("*No Times Specified*")
                return

            # Find the user in the db, stop if not found
            user_from_db = await self._get_user_by_id(time_author.id)
            if user_from_db is None or user_from_db.tz == "":
                return
            await message_by_bot.edit(
                content=await self._reply_from_user_and_times(user_from_db, time_list)
            )

    async def conversion_handler(self, message: d.Message):
        # Pull properties we want from the message
        user_id = message.author.id
        content = message.content

        time_list = await self._time_list_from_string(content)

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
                "You haven't registered with me yet\n"
                + "Sending you a registration link in a dm..."
            )
            await response_msg.add_reaction(MESSAGE_REFRESH_REACTION)
            await response_msg.add_reaction(MESSAGE_DELETE_REACTION)
            await self.register_user(message)
            while True:
                await asyncio.sleep(10)
                user: User = await self._get_user_by_id(user_id)
                if dt.datetime.now(tz=utc) - user.update_dt > REGISTRATION_TIMEOUT:
                    await response_msg.delete()
                    break
                elif user is None or user.tz == "":
                    continue
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
            await response_msg.add_reaction(MESSAGE_REFRESH_REACTION)
            await response_msg.add_reaction(MESSAGE_DELETE_REACTION)

    async def pizza_message_handler(self, message: d.Message):
        if ("pizza" in message.content.lower()) or ("🍕" in message.content):
            await message.add_reaction("🍕")
        if ("taco" in message.content.lower()) or ("🌮" in message.content) and self.extra_features:
            await message.add_reaction("🌮")

    async def registration_handler(self, message: d.Message):
        if message.content == "?time" and (
            message.channel.id == self.reg_channel_id or self.reg_channel_id is None
        ):
            await self.register_user(message)
            await message.reply("Check your direct messages for a registration link")

    async def register_user(self, message: d.Message):
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
                            dt.datetime.now(tz=utc) - User.update_dt
                            <= REGISTRATION_TIMEOUT
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

    async def on_member_update(self, before, after):
        patron_role = self.client.get_guild(self.server_id).get_role(cfg.patron_role_id)
        patron_channel = self.client.get_channel(cfg.patrons_channel_id)
        if patron_role in before.roles:
            return
        if patron_role in after.roles:
            await patron_channel.send(cfg.patrons_welcome_text.format(after.id))

    @staticmethod
    async def _get_user_by_id(id: int) -> Union[User, None]:
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

    @staticmethod
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
