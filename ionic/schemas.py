import asyncio
import datetime as dt

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql.schema import Column
from sqlalchemy.sql.sqltypes import TIMESTAMP, VARCHAR, BigInteger

from . import cfg

Base = declarative_base()


class User(Base):
    __tablename__ = "user"
    __mapper_args__ = {"eager_defaults": True}
    id = Column("id", BigInteger, primary_key=True)
    tz = Column("tz", VARCHAR(64))
    # Column used to mark a user for an update
    update_id = Column("update_id", BigInteger)
    update_dt = Column("update_dt", TIMESTAMP)

    def __init__(self, id, tz):
        super().__init__()
        self.id = id
        self.tz = tz
        self.update_dt = dt.datetime.now(tz=dt.timezone.utc)


async def recreate_all():
    # db_engine = create_engine(cfg.db_url, connect_args=cfg.db_connect_args)
    db_engine = create_async_engine(cfg.db_url_async, connect_args=cfg.db_connect_args)
    # db_session = sessionmaker(db_engine, **cfg.db_session_kwargs)

    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(recreate_all())
