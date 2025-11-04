from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import inspect as sa_inspect

from .config import DATABASE_URL

Base = declarative_base()
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db_and_migrate():
    Base.metadata.create_all(bind=engine)
    try:
        inspector = sa_inspect(engine)
        cols = [c.get('name') or c.get('name_') for c in inspector.get_columns('users')]
        if 'avatar_filename' not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar_filename VARCHAR"))
                conn.commit()
    except Exception:
        pass
