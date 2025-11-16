"""BotLog model."""
from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from src.core.database import Base


class BotLog(Base):
    """BotLog model for storing bot activity logs."""

    __tablename__ = "bot_logs"

    id = Column(Integer, primary_key=True, index=True)
    grid_bot_id = Column(Integer, ForeignKey("grid_bots.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)

    # Log details: info, warning, error
    log_level = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSONB, nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)

    # Relationships
    grid_bot = relationship("GridBot", back_populates="logs")
    user = relationship("User", back_populates="bot_logs")

    def __repr__(self):
        return f"<BotLog(id={self.id}, level={self.log_level}, message={self.message[:50]})>"

    @classmethod
    def create_info(cls, message: str, grid_bot_id: int = None, user_id: int = None, details: dict = None):
        """Create info log entry."""
        return cls(
            log_level="info",
            message=message,
            grid_bot_id=grid_bot_id,
            user_id=user_id,
            details=details
        )

    @classmethod
    def create_warning(cls, message: str, grid_bot_id: int = None, user_id: int = None, details: dict = None):
        """Create warning log entry."""
        return cls(
            log_level="warning",
            message=message,
            grid_bot_id=grid_bot_id,
            user_id=user_id,
            details=details
        )

    @classmethod
    def create_error(cls, message: str, grid_bot_id: int = None, user_id: int = None, details: dict = None):
        """Create error log entry."""
        return cls(
            log_level="error",
            message=message,
            grid_bot_id=grid_bot_id,
            user_id=user_id,
            details=details
        )
