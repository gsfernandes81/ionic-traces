import datetime as dt
import re
import ssl
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


# Constants
REGISTRATION_TIMEOUT = dt.timedelta(minutes=10)
MESSAGE_DELETE_REACTION = "❌"
MESSAGE_REFRESH_REACTION = "🔄"
EMOJI_GUILD = 920027638179966996
SWEET_BUSINESS = 1047050852994662400
CORPORATE_SPONSORSHIP = 1047672106688712794
DOWN_TO_BUSINESS = 1047673578012819536
GO_ABOUT_YOUR_BUSINESS = 1047673598686527508
TELESTO = 1047086753271533608
PILK = 1047097563129598002
HIO_UID = 803658060849217556
BRYCE_UID = 204985399926456320
ASTROCYTE_LORE = [
    line.lower()
    for line in [
        "Ghost, record this.",
        "Trial 1: I am now putting the Astrocyte Verse on my",
        "Ending",
        "Beginning of all endings",
        "Dying into infinite composite",
        "All nothings begin therewhen",
        "Fear is very small and it is everywhy and it is not fear it is a brutal spark a nerve ending straining under weight multimyr iteration could not foresee even though it is just that because there is no other—",
        "Acausals whickering away become jagged umami zeroes",
        "Awe yourself toward reddening shift",
        "[Ghost note: key of Eb minor]",
        "[silence lasting 4.22 minutes]",
        "Good work, Ghost. Now, let's go again.",
        "Trial 93. I am now putting the Astrocyte Verse on my head—",
    ]
]
