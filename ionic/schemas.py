import datetime as dt

from pytz import utc
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql.schema import Column
from sqlalchemy.sql.sqltypes import BigInteger, DateTime, String

Base = declarative_base()


class User(Base):
    __tablename__ = "mbd_user"
    __mapper_args__ = {"eager_defaults": True}
    id = Column("id", BigInteger, primary_key=True)
    tz = Column("tz", String)
    # Column used to mark a user for an update
    update_id = Column("update_id", BigInteger)
    update_dt = Column(
        "update_dt", DateTime(timezone=True), default=dt.datetime.now(tz=utc)
    )

    def __init__(self, id, tz):
        super().__init__()
        self.id = id
        self.tz = tz
