# Define our custom discord bot classes
# This is the base lightbulb.BotApp but with added utility functions

import datetime as dt
from typing import Dict, List, Union

import hikari as h
import lightbulb as lb
import regex as re

from . import cfg


class CachedFetchBot(lb.BotApp):
    """lb.BotApp subclass with async methods that fetch objects from cache if possible"""

    async def fetch_channel(self, channel_id: int):
        """This method fetches a channel from the cache or from discord if not cached"""
        return self.cache.get_guild_channel(
            channel_id
        ) or await self.rest.fetch_channel(channel_id)

    async def fetch_guild(self, guild_id: int):
        """This method fetches a guild from the cache or from discord if not cached"""
        return self.cache.get_guild(guild_id) or await self.rest.fetch_guild(guild_id)

    async def fetch_message(
        self, channel: h.SnowflakeishOr[h.TextableChannel], message_id: int
    ):
        """This method fetches a message from the cache or from discord if not cached

        channel can be the channels id or the channel object itself"""
        if isinstance(channel, h.Snowflake) or isinstance(channel, int):
            # If a channel id is specified then get the channel for that id
            # I am not sure if the int check is necessary since Snowflakes
            # are subcalsses of int but want to test this later and remove
            # it only after double checking. Most likely can remove, and I'm
            # just being paranoid
            channel = await self.fetch_channel(channel)

        return self.cache.get_message(message_id) or await self.rest.fetch_message(
            channel, message_id
        )

    async def fetch_emoji(self, guild_id, emoji_id):
        """This method fetches an emoji from the cache or from discord if not cached"""
        # TODO allow passing a guild (not id) to this method as well for convenience
        return self.cache.get_emoji(emoji_id) or await self.rest.fetch_emoji(
            guild_id, emoji_id
        )

    async def fetch_user(self, user: int):
        """This method fetches a user from the cache or from discord if not cached"""
        return self.cache.get_user(user) or await self.rest.fetch_user(user)


class SpecialFeaturesBot(CachedFetchBot):
    """Bot implementation with special reaction related features"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dict of reactions -> Dict of user_ids to react to -> time to react till
        # This is a dict is structured like so
        # reactirs_register: {
        #
        #   reaction (of type h.Emoji or str): {
        #       user id (of type int): time to react until (in dt.datetime)
        #   }
        # }
        #
        # Users & reactions are added to this dict by cls.react_storm_user_for(...)
        # and removed by cls.undo_react_storm_user(...)
        self.reactors_register: Dict[Union[str, h.Emoji], Dict[int, dt.datetime]] = {}
        # The above dict is used by the below method which is a staticmethod
        # that listens to the GuildMessageCreate event
        self.listen()(self._user_reactor)

    def react_to_guild_messages(
        self,
        trigger_regex: re.Pattern,
        reaction: Union[str, h.Emoji],
        allowed_servers: List[int] = [],
        allowed_uids: List[int] = [],
    ):
        """React to guild messages

        with 'reaction' (either a unicode emoji or a h.Emoji object)
        in 'allowed_servers' (list of allowed server ids)
        if message is by user in 'allowed_uids' (allowed user ids, list)
        and if message the pattern in 'trigger_regex' can be found in message.content"""

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

        return self.listen()(reaction_handler)

    def react_to_guild_reactions(
        self,
        trigger_regex: re.Pattern,
        allowed_servers: List[int] = [],
        allowed_uids: List[int] = [],
    ):
        """React to guild reactions

        with the reaction itself
        in 'allowed_servers' (list of allowed server ids)
        if reaction is by user in 'allowed_uids' (allowed user ids, list)
        and if message the pattern in 'trigger_regex' can be found in message.content"""

        async def reaction_handler(event: h.GuildReactionAddEvent):
            bot: SpecialFeaturesBot = event.app
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

        return self.listen()(reaction_handler)

    async def react_storm_user_for(
        self,
        time: dt.timedelta,
        user: h.SnowflakeishOr[h.User],
        reaction: Union[str, h.Emoji],
    ):
        """Reacts to <user> with <reaction> for <time> in discord guilds

        This is a non blocking function"""
        if isinstance(user, h.Snowflake) or isinstance(user, int):
            user_id = user
        else:
            user_id = user.id

        react_till = dt.datetime.now() + time

        if reaction not in self.reactors_register:
            self.reactors_register[reaction] = {}

        try:
            # Update the reactors_register with the user id and/or react till time
            self.reactors_register[reaction][user_id] = max(
                self.reactors_register[reaction][user_id], react_till
            )
        except KeyError:
            # If user_id is not in self.reactors_register[reaction]
            # put it there
            self.reactors_register[reaction][user_id] = react_till

    async def undo_react_storm_user(
        self,
        user: h.SnowflakeishOr[h.User],
        reaction: Union[str, h.KnownCustomEmoji],
    ):
        "Undoes the effect of cls.react_storm_user_for(...)"
        if isinstance(user, h.Snowflake) or isinstance(user, int):
            user_id = user
        else:
            user_id = user.id

        if reaction not in self.reactors_register:
            if not (
                isinstance(reaction, h.KnownCustomEmoji)
                and (
                    reaction.guild_id,
                    reaction.id,
                )
                in [
                    (
                        r.guild_id,
                        r.id,
                    )
                    for r in self.reactors_register
                ]
            ):
                return

        # Update the reactors_register with the user id and/or react till time
        self.reactors_register[reaction].pop(user_id, "")

    def react_storm_user_on_message(
        self,
        trigger_regex: re.Pattern,
        reactions: List[Union[str, h.Emoji]],
        allowed_servers: List[int] = [],
        allowed_uids: List[int] = [],
    ):
        """Run react_storm_user_for if 'trigger_regex' is matched in message content

        reacting with 'reactions'
        if regex matched a message in 'allowed_servers' (List of server ids)
        if message sent by user in 'allowed_uids' (List of user ids)
        """

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
                for reaction in reactions:
                    await self.react_storm_user_for(
                        dt.timedelta(minutes=2), user_id, reaction
                    )

        self.listen()(reaction_handler)

    @staticmethod
    async def _user_reactor(event: h.GuildMessageCreateEvent):
        for reaction, user_dict in event.app.reactors_register.items():
            try:
                if (
                    event.guild_id in cfg.pizza_servers
                    and dt.datetime.now() < user_dict[event.author_id]
                ):
                    await event.message.add_reaction(reaction)
            except KeyError:
                # Ignore the event if user_dict throws a keyerror with author_id
                # since this means the author is not tagged for any reactions
                # on their messages
                pass
