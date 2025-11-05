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
        # users.avatar_filename
        cols = [c.get('name') or c.get('name_') for c in inspector.get_columns('users')]
        if 'avatar_filename' not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar_filename VARCHAR"))
                conn.commit()
        if 'is_deleted' not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
                conn.commit()

        # events.allow_wp_only, events.is_deleted
        evt_cols = [c.get('name') or c.get('name_') for c in inspector.get_columns('events')]
        with engine.connect() as conn:
            if 'allow_wp_only' not in evt_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN allow_wp_only BOOLEAN DEFAULT 0"))
                conn.commit()
            if 'is_deleted' not in evt_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
                conn.commit()
            if 'event_type_id' not in evt_cols:
                conn.execute(text("ALTER TABLE events ADD COLUMN event_type_id INTEGER"))
                conn.commit()

        # challenges.is_deleted
        ch_cols = [c.get('name') or c.get('name_') for c in inspector.get_columns('challenges')]
        if 'is_deleted' not in ch_cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE challenges ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
                conn.commit()

        # submissions.manual_points
        sub_cols = [c.get('name') or c.get('name_') for c in inspector.get_columns('submissions')]
        if 'manual_points' not in sub_cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN manual_points FLOAT"))
                conn.commit()
        # submissions.is_deleted
        sub_cols = [c.get('name') or c.get('name_') for c in inspector.get_columns('submissions')]
        if 'is_deleted' not in sub_cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
                conn.commit()
    except Exception:
        pass
