"""User model."""
from sqlalchemy import BigInteger, Boolean, Column, Integer, String, DECIMAL, TIMESTAMP, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from src.core.database import Base


class User(Base):
    """User model for storing Telegram user data and settings."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)

    # MEXC API credentials (encrypted)
    mexc_api_key = Column(Text, nullable=True)
    mexc_api_secret = Column(Text, nullable=True)

    # Settings
    language = Column(String(10), default="ru")
    notifications_enabled = Column(Boolean, default=True)
    notify_order_filled = Column(Boolean, default=True)
    notify_profit = Column(Boolean, default=True)
    notify_errors = Column(Boolean, default=True)
    daily_summary = Column(Boolean, default=False)
    profit_notify_percent = Column(DECIMAL(5, 2), default=5.0)

    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    last_active_at = Column(TIMESTAMP, server_default=func.now())

    # Relationships
    grid_bots = relationship("GridBot", back_populates="user", cascade="all, delete-orphan")
    bot_logs = relationship("BotLog", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, telegram_id={self.telegram_id}, username={self.username})>"

    @property
    def has_api_keys(self) -> bool:
        """Check if user has API keys configured."""
        return bool(self.mexc_api_key and self.mexc_api_secret)

    @property
    def full_name(self) -> str:
        """Get user's full name."""
        parts = []
        if self.first_name:
            parts.append(self.first_name)
        if self.last_name:
            parts.append(self.last_name)
        return " ".join(parts) if parts else self.username or f"User {self.telegram_id}"
