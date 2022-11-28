import datetime as dt
import ssl
import re
from os import getenv as _getenv

from sqlalchemy.ext.asyncio import AsyncSession

# Discord API Token
discord_token = _getenv("DISCORD_TOKEN")


# Pizza enabled servers
pizza_servers = str(_getenv("PIZZA_SERVER_LIST")).strip().split(",")
pizza_servers = [int(server.strip()) for server in pizza_servers]

# Taco enabled servers
taco_servers = str(_getenv("TACO_SERVER_LIST")).strip().split(",")
taco_servers = [int(server.strip()) for server in taco_servers]

# DMB Patrons special config
patron_role_id, patrons_channel_id, patrons_welcome_text = (
    str(_getenv("DMB_PATRONS_CONFIG")).strip().split(",")
)
patron_role_id = int(patron_role_id)
patrons_channel_id = int(patrons_channel_id)
patrons_welcome_text = patrons_welcome_text.strip()

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

# Url for the bot and scheduler db
db_url = _getenv("MYSQL_URL")
# mysql+aiomysql://user:password@host:port/dbname[?key=value&key=value...]
repl_till = db_url.find("://")
db_url = db_url[repl_till:]
db_url_async = "mysql+asyncmy" + db_url
db_url = "mysql" + db_url


db_session_kwargs_sync = {
    "expire_on_commit": False,
}

# Async SQLAlchemy DB Session KWArg Parameters
db_session_kwargs = db_session_kwargs_sync | {
    "class_": AsyncSession,
}

ssl_ctx = ssl.create_default_context(cafile="/etc/ssl/certs/ca-certificates.crt")
ssl_ctx.verify_mode = ssl.CERT_REQUIRED
db_connect_args = {"ssl": ssl_ctx}

REGISTRATION_TIMEOUT = dt.timedelta(minutes=10)
