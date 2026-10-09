from alembic import context

from wecomarchive.db.models import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("请调用 wecomarchive.migration.upgrade(engine) 并传入数据库引擎")
context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
