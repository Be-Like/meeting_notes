import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base

# Database configuration
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./meetings.db")

# Create engine
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_tables():
    """
    Create all tables in the database.
    """
    Base.metadata.create_all(bind=engine)


def get_db():
    """
    Dependency to get database session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
