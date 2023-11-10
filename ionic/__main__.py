import argparse
import asyncio
import datetime as dt
import random
import sys
from typing import List, Union

import aiodebug.log_slow_callbacks
import dateparser
import emoji
import hikari as h
import lightbulb as lb
import regex as re
import sqlalchemy as sql
import uvloop
from arrow import Arrow
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.expression import delete, select

from . import cfg
from .bot import SpecialFeaturesBot
from .cfg import REGISTRATION_TIMEOUT
from .schemas import Base, User

aiodebug.log_slow_callbacks.enable(0.05)

db_engine = create_async_engine(cfg.db_url_async, connect_args=cfg.db_connect_args)
db_session = sessionmaker(db_engine, **cfg.db_session_kwargs)


# Regex datetime markers excluding discord elements
rgx_dt_markers = re.compile(
    "(?!<(@|!|#|@!|@&)[0-9]+>|<a{0,1}:[a-zA-Z0-9_.]{2,32}:[0-9]+>|<t:[0-9]+:[a-zA-Z]{0,1}>)(<[^>]+>)"
)
# Regex get user from string with discord @user and nothing else
rgx_d_user = re.compile("^<@(\d+)>$")


# Bot subclass with convenience functions built in
bot = SpecialFeaturesBot(
    cfg.discord_token,
    intents=(
        h.Intents.ALL_UNPRIVILEGED
        | h.Intents.GUILD_MEMBERS
        | h.Intents.MESSAGE_CONTENT
        | h.Intents.ALL_MESSAGE_REACTIONS
    ),
)


async def update_status(guild_count: int):
    await bot.update_presence(
        activity=h.Activity(
            name="{} servers : )".format(guild_count),
            type=h.ActivityType.LISTENING,
        )
    )


@bot.listen()
async def on_start(event: lb.LightbulbStartedEvent):
    bot.d.guild_count = len(await bot.rest.fetch_my_guilds())
    await update_status(bot.d.guild_count)


@bot.listen()
async def on_guild_add(event: h.GuildJoinEvent):
    bot.d.guild_count += 1
    await update_status(bot.d.guild_count)


@bot.listen()
async def on_guild_rm(event: h.GuildLeaveEvent):
    bot.d.guild_count -= 1
    await update_status(bot.d.guild_count)


async def _time_list_from_string(text: str) -> List[dt.datetime]:
    """Converts a string to a parsed list of dt.datetimes

    - Takes the text,
    - pulls out everything surrounded by <> that isn't a discord element
    - puts each of these into a list (with the angle brackets themselves excluded)
    - removes any links if they were picked up
    - parse these into datetime objects
    return the list of datetime objects
    """
    # Find time tokens
    time_list = rgx_dt_markers.findall(text)
    # Bring out the second capturing groups in the regex matches list
    # Note, the first is the negative lookahead for discord elements
    time_list = [time[1] for time in time_list]
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
    """Returns the user or None if they aren't found in the timezone db"""
    async with db_session() as session:
        async with session.begin():
            user = (
                await session.execute(select(User).where(User.id == int(id)))
            ).fetchone()
    return user if user is None else user[0]


async def _convert_time_list_fm_user(user: User, time_list: List) -> List[str]:
    """Takes a user and times in their zone and converts it to utc unix time

    When given a user (from the timezone db) and a time list (of dt.datetime objs)
    this function will convert the time list into utc time,
    then convert these into unix time (seconds since epoch start)
    then convert these into a list of discord times that auto convert for everyone
    assuming the user is speaking in their own time zone"""
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


async def _embed_from_user_times_and_text(
    user: User, time_list: List, text: str
) -> h.Embed:
    """Substitue times into <text> after converting them for <user>"""
    time_list = await _convert_time_list_fm_user(user, time_list)
    reply = text
    for time in time_list:
        reply = rgx_dt_markers.sub(time, reply, count=1)

    return h.Embed(description=reply, colour=cfg.EXOTIC_YELLOW)


async def _add_user_persona_to_embed(event: h.MessageCreateEvent, embed: h.Embed):
    """Takes an embed and adds the user's profile from an event into the embed"""
    if isinstance(event, h.GuildMessageCreateEvent):
        user = event.member
    else:
        user = event.author

    embed.set_author(
        name="{}#{}".format(user.username, user.discriminator),
        icon=user.avatar_url,
    )

    if user.accent_color:
        embed.color = user.accent_color

    return embed


async def register_user(message: h.Message):
    """Adds a user to the db and sends them a link to add their time zone there"""
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
    """Remove the user entirely from the timezone db"""
    # Find the user in the db
    async with db_session() as session:
        async with session.begin():
            # Delete the user's row
            await session.execute(delete(User).where(User.id == int(ctx.author.id)))

    await ctx.respond("You have successfully deregistered")


@bot.listen()
async def time_message_handler(event: h.MessageCreateEvent):
    """Coroutine to handle time conversions

    If a message has time markers detected (rgx_dt_markers),
    then these are extracted, converted and sent back to the user
    if the user is registered. If the user isn't registered, then
    a registration dm is sent, and the message is edited with the
    times when the user registers."""
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
            embed = await _embed_from_user_times_and_text(user, time_list, content)
            embed = await _add_user_persona_to_embed(event=event, embed=embed)
            try:
                await response_msg.edit(content="", embed=embed)
            except h.NotFoundError:
                # Ignore this error: (The message must have been deleted)
                pass
            break
    else:
        # Use the time list and the user object to create a reply
        embed = await _embed_from_user_times_and_text(user, time_list, content)
        embed = await _add_user_persona_to_embed(event=event, embed=embed)
        response_msg = await message.respond(content="", embed=embed, reply=True)
        # Replace the below with buttons
        # await response_msg.add_reaction(MESSAGE_REFRESH_REACTION)
        # await response_msg.add_reaction(MESSAGE_DELETE_REACTION)


@bot.listen()
async def pre_start(event: h.StartingEvent):
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@bot.command
@lb.option(name="iii", description="All nothings begin therewhen", default="iii")
@lb.option(name="ii", description="Dying into infinite composite", default="ii")
@lb.option(name="i", description="Beginning of all endings", default="i")
@lb.command("verse", "Ending", ephemeral=True, guilds=cfg.pizza_servers)
@lb.implements(lb.SlashCommand)
async def sh(ctx: lb.Context):
    """Just me having some fun :)"""
    bot: SpecialFeaturesBot = ctx.bot
    cmd = ctx.options.i.lower()
    arg1 = ctx.options.ii.lower()
    arg2 = ctx.options.iii.lower()

    cmd = None if cmd == "i" else cmd
    arg1 = None if arg1 == "ii" else arg1
    arg2 = None if arg2 == "iii" else arg2

    if ctx.author.id not in await ctx.bot.fetch_owner_ids():
        await ctx.respond(content=">:)")
        for owner_id in await bot.fetch_owner_ids():
            owner = await bot.fetch_user(owner_id)
            owner_dm = await owner.fetch_dm_channel()
            await owner_dm.send(
                embed=h.Embed(
                    title="Unauthorized verse attempt",
                    description=(
                        "Note\n"
                        + "{author.username}#{author.discriminator} "
                        + "attempted to use:\n\n```\n"
                        + "/verse i:{cmd} ii:{arg1} iii:{arg2}\n```\n"
                        + "Appropriate action was taken >:)"
                    ).format(
                        author=ctx.author,
                        cmd=cmd or "<blank>",
                        arg1=arg1 or "<blank>",
                        arg2=arg2 or "<blank>",
                    ),
                )
            )

        await bot.react_storm_user_for(
            dt.timedelta(hours=1),
            ctx.author,
            await bot.fetch_emoji(cfg.EMOJI_GUILD, cfg.PILK),
        )
    elif cmd in ["spilk", "pilk"]:
        if cmd == "pilk":
            # pilk -> (Loud) Pizza Milk
            await ctx.respond(content=">:)", flags=h.MessageFlag.NONE)
        else:
            # Spilk -> Silent Pizza Milk
            await ctx.respond(content=">:)", flags=h.MessageFlag.EPHEMERAL)
        user_id = int(rgx_d_user.match(arg1).group(1))
        if arg2 is not None:
            minutes = int(arg2)
            await bot.react_storm_user_for(
                time=dt.timedelta(minutes=minutes),
                user=user_id,
                reaction=await bot.fetch_emoji(cfg.EMOJI_GUILD, cfg.PILK),
            )
        else:
            await bot.react_storm_user_for(
                dt.timedelta(hours=1),
                user_id,
                await bot.fetch_emoji(cfg.EMOJI_GUILD, cfg.PILK),
            )
    elif cmd == "restart":
        await ctx.respond(
            content="Are you sure you want to restart?",
            component=bot.rest.build_message_action_row()
            .add_button(h.ButtonStyle.DANGER, "restart_button_yes")
            .set_label("Yes")
            .add_to_container()
            .add_button(h.ButtonStyle.PRIMARY, "restart_button_no")
            .set_label("No")
            .add_to_container(),
            flags=h.MessageFlag.EPHEMERAL,
        )

        event = await bot.wait_for(
            h.InteractionCreateEvent,
            timeout=30,
            predicate=lambda e: isinstance(e.interaction, h.ComponentInteraction)
            and e.interaction.custom_id in ["restart_button_yes", "restart_button_no"],
        )
        if event.interaction.custom_id == "restart_button_yes":
            await event.interaction.create_initial_response(
                h.ResponseType.MESSAGE_UPDATE, "Bot is restarting now"
            )
            sys.exit(1)
        else:
            await event.interaction.create_initial_response(
                h.ResponseType.MESSAGE_UPDATE, "Bot will not restart"
            )
    elif (
        cmd in cfg.ASTROCYTE_LORE
        and arg1 in cfg.ASTROCYTE_LORE
        and arg2 in cfg.ASTROCYTE_LORE
        and cmd not in [arg1, arg2]
        and arg1 != arg2
    ):
        await ctx.respond(
            embed=h.Embed(
                title="<:verse:1047672073109110925> Astrocyte Verse",
                description="The ideocosm contained within this helm transforms "
                + "the wearer's head from flesh and/or exoneurons to the pure, "
                + "raw stuff of thought.",
            ),
        )
        await bot.undo_react_storm_user(
            ctx.author.id, await bot.fetch_emoji(cfg.EMOJI_GUILD, cfg.PILK)
        )
    else:
        await ctx.respond(content="Command not found")


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
        allowed_uids=await bot.fetch_owner_ids() + [cfg.HIO_UID],
    )
    bot.react_to_guild_messages(
        trigger_regex=re.compile(
            "long\s+live\s+the\s+queen",
            flags=re.IGNORECASE,
        ),
        reaction=await bot.fetch_emoji(cfg.EMOJI_GUILD, cfg.TELESTO),
        allowed_servers=cfg.pizza_servers,
    )

    # Sweet business reactions for Bryce
    bot.react_to_guild_reactions(
        trigger_regex=re.compile("^sweet[_ ]business$", flags=re.IGNORECASE),
        allowed_servers=cfg.pizza_servers,
        allowed_uids=await bot.fetch_owner_ids() + [cfg.BRYCE_UID],
    )
    for emoji_id in [
        cfg.SWEET_BUSINESS,
        cfg.CORPORATE_SPONSORSHIP,
        cfg.DOWN_TO_BUSINESS,
        cfg.GO_ABOUT_YOUR_BUSINESS,
    ]:
        bot.react_to_guild_messages(
            trigger_regex=re.compile(
                "^(…|\.\.\.)I love my job\.$", flags=re.RegexFlag.IGNORECASE
            ),
            reaction=await bot.fetch_emoji(cfg.EMOJI_GUILD, emoji_id),
            allowed_uids=await bot.fetch_owner_ids() + [cfg.BRYCE_UID],
        )
    bot.react_storm_user_on_message(
        trigger_regex=re.compile("^(…|\.\.\.)I love my job\.$", flags=re.IGNORECASE),
        allowed_servers=cfg.pizza_servers,
        allowed_uids=await bot.fetch_owner_ids() + [cfg.BRYCE_UID],
        reactions=[
            await bot.fetch_emoji(cfg.EMOJI_GUILD, emoji_id)
            for emoji_id in [
                cfg.SWEET_BUSINESS,
                cfg.CORPORATE_SPONSORSHIP,
                cfg.DOWN_TO_BUSINESS,
                cfg.GO_ABOUT_YOUR_BUSINESS,
            ]
        ],
    )
    # The forbidden emoji
    bot.react_to_guild_reactions(
        trigger_regex=re.compile("fu+[c]{0,1}kbo[yi]+$", flags=re.IGNORECASE),
        allowed_servers=cfg.pizza_servers,
    )


@bot.command()
@lb.option("channel", "Channel to search", h.TextableGuildChannel, default=None)
@lb.option(
    "hours",
    "How far back to pull messages from",
    int,
    default=6,
    max_value=24,
    min_value=1,
)
@lb.option("nojoy", "Ignores 🤣 and 😂 reactions", bool, default=True)
@lb.command(
    "reactionrank",
    "List top 5 reactions in specified channel",
    guilds=cfg.pizza_servers,
    auto_defer=True,
)
@lb.implements(lb.SlashCommand)
async def reaction_rank(ctx: lb.Context) -> None:
    """Command takes channel argument and optional hours argument and
    returns embed of reactions pulled from messages in the channel"""

    target_datetime = dt.datetime.now() - dt.timedelta(hours=ctx.options.hours)

    channel = await bot.fetch_channel(ctx.options.channel or ctx.channel_id)

    # Check if the channel is textable
    if not isinstance(channel, h.TextableGuildChannel):
        await ctx.respond(f"This command only works on textable channels")
        return

    # Grabs Lazy Iterator of all messages AFTER the target_datetime
    channel_history = channel.fetch_history(after=target_datetime)
    reaction_dict = {}
    message_count = 0

    # We iterate through each message, and check its reactions. If a reaction is
    # NOT in dictionary, we set a key as the reactions text name, and set its value
    # to be another dictionary with the running total "count" key and "object"
    # holding the actual Emoji object itself
    async for message in channel_history:
        for reaction in message.reactions:
            if reaction.emoji.name not in reaction_dict.keys():
                reaction_dict[reaction.emoji.name] = {
                    "count": reaction.count,
                    "object": reaction.emoji,
                }
                if reaction.is_me:
                    reaction_dict[reaction.emoji.name]["count"] -= 1
            else:
                reaction_dict[reaction.emoji.name]["count"] += reaction.count
                # This subtractions are so we dont count the bot's emoji
                if reaction.is_me:
                    reaction_dict[reaction.emoji.name]["count"] -= 1
        message_count += 1

    delete_empty = [key for key in reaction_dict if reaction_dict[key]["count"] < 1]
    # Checking for and deleting any emoji with 0 count
    for key in delete_empty:
        del reaction_dict[key]

    # A quick check to see if any emoji were found
    if not reaction_dict:
        await ctx.respond("No reactions found : (")
        return

    # Remove the below laughing emoji if requested
    if ctx.options.nojoy:
        reaction_dict.pop("😂", None)
        reaction_dict.pop("🤣", None)
        nojoy_str = "\n(Laughing reactions excluded)"
        # Add text to title_embed description indicating bool
    else:
        nojoy_str = ""

    reaction_list = list(reaction_dict.items())
    # Sort listed emoji from most frequent to least frequent
    reaction_list.sort(key=lambda x: x[1]["count"], reverse=True)

    # List of embeds containing reactions to iterate through
    reaction_embeds = []

    # This embed lays out the statistics of emojis vs messages searched.
    title_embed = h.Embed(
        title="Reaction Rankings",
        description=f"Here are the top most used reactions from the past "
        + f"`{ctx.options.hours}` hours in <#{channel.id}>{nojoy_str}",
        color=h.Color(0x0099FF),
    )
    title_embed.add_field(
        name="Messages", value=f"`{message_count}` messages searched", inline=True
    )
    title_embed.add_field(
        name="Reactions",
        value=f"`{len(reaction_dict.keys())}` reactions found",
        inline=True,
    )
    title_embed.add_field(
        name="Average",
        value="{} reactions per message".format(
            round(len(reaction_dict.keys()) / message_count, 2)
            if message_count != 0
            else 0
        ),
        inline=True,
    )
    reaction_embeds.append(title_embed)

    # Iterate through the list of unique emoji and turn the top three into their
    # own embed, then append the embed to the reaction_emmbed list
    for index, reaction in enumerate(reaction_list[:3]):
        unicode_medal = cfg.RANK_EMOJI_MEDALS[index]

        # Check if emoji is unicode, then turn it into its unicode name,
        # else just return the Custom Emoji name
        if isinstance(reaction[1]["object"], h.UnicodeEmoji):
            emoji_name = (
                emoji.demojize(reaction[1]["object"].name)
                .replace(":", "")
                .replace("_", " ")
            )
        else:
            emoji_name = reaction[1]["object"].name

        embed = h.Embed(
            title=f"Rank {index + 1}  " + chr(int(unicode_medal)) + ":",
            description=f'`{emoji_name.lower()}`\nUsed {reaction[1]["count"]} times',
            color=h.Color(cfg.RANK_EMBED_COLORS[index]),
        )
        embed.set_thumbnail(reaction[1]["object"].url)
        reaction_embeds.append(embed)

    await ctx.respond(embeds=reaction_embeds)
    return


def main():
    """Install uvloop and start the bot"""
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
