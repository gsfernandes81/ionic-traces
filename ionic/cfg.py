from os import getenv as _getenv

from sqlalchemy.ext.asyncio import AsyncSession

# Discord API Token
discord_token = _getenv("DISCORD_TOKEN")

# Server List
# Format as follows
# Servers separated by commas ","
# Server with separate registration channels have
# Registration channel ids after : before the next comma
# Whitespaces are allowed before/after ids and symbols
server_list = str(_getenv("SERVER_LIST")).strip()
server_list = server_list.split(",")
server_list = [server.strip() for server in server_list]
server_list = [server.split(":") for server in server_list]
server_list = [[element.strip() for element in server] for server in server_list]


# Registration URL
app_url = str(_getenv("APP_URL"))
if app_url.startswith("https") and not _getenv("HTTPS_ENABLED").lower() == "true":
    app_url = app_url[5:]
    app_url = "http" + app_url
if app_url.startswith("http:") and _getenv("HTTPS_ENABLED").lower() == "true":
    app_url = app_url[4:]
    app_url = "https" + app_url
if app_url.endswith("/"):
    app_url = app_url[:-1]

port = str(_getenv("PORT"))

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
