import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from typing import AsyncGenerator

# Nur der Rueckfall fuer einen Start ohne DATABASE_URL — im Betrieb setzt das
# Startskript des LaunchDaemon die Variable auf einen absoluten Pfad. Der Pfad hier
# ist relativ zum Arbeitsverzeichnis: aus einem Unterordner gestartet legt der
# Dienst sonst eine zweite, leere Datenbank an statt die vorhandene zu oeffnen.
# Ein Rechner aus der Tontechnik-Zeit hat data/tontechnik.db; autostart/install.sh
# erkennt das und traegt den vorhandenen Pfad ein.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/akouo.db")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async database session."""
    async with AsyncSessionLocal() as session:
        yield session
