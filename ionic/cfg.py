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
