from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
DB_DSN = f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME')}"
engine = create_engine(DB_DSN, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()
