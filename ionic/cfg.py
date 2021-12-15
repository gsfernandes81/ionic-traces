from os import getenv as _getenv

from sqlalchemy.ext.asyncio import AsyncSession

# Discord API Token
discord_token = _getenv("IONIC_DISCORD_TOKEN")

# MBD Server ID
mbd_server_id: int = int(_getenv("MBD_SERVER_ID"))

# Bot Channel ID
bot_channel_id = int(_getenv("MBD_BOT_CHANNEL_ID"))

# Url for the bot and scheduler db
# SQAlchemy doesn't play well with postgres://, hence we replace
# it with postgresql://
db_url = _getenv("DATABASE_URL")
if db_url.startswith("postgres"):
    repl_till = db_url.find("://")
    db_url = db_url[repl_till:]
    db_url_async = "postgresql+asyncpg" + db_url
    db_url = "postgresql" + db_url

# Async SQLAlchemy DB Session KWArg Parameters
db_session_kwargs = {"expire_on_commit": False, "class_": AsyncSession}
