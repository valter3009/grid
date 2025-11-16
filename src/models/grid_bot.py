"""GridBot model."""
from sqlalchemy import Column, Integer, String, DECIMAL, TIMESTAMP, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from src.core.database import Base


class GridBot(Base):
    """GridBot model for storing grid trading bot configuration and statistics."""

    __tablename__ = "grid_bots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Bot identification
    bot_name = Column(String(255), nullable=True)

    # Trading pair
    symbol = Column(String(50), nullable=False)  # e.g., BTC/USDT
    base_currency = Column(String(20), nullable=True)  # e.g., BTC
    quote_currency = Column(String(20), nullable=True)  # e.g., USDT

    # Grid parameters
    lower_price = Column(DECIMAL(20, 8), nullable=False)
    upper_price = Column(DECIMAL(20, 8), nullable=False)
    grid_levels = Column(Integer, nullable=False)
    investment_amount = Column(DECIMAL(20, 8), nullable=False)

    # Grid type (для MVP только arithmetic)
    grid_type = Column(String(20), default="arithmetic")

    # Status: active, paused, stopped
    status = Column(String(20), default="active", index=True)

    # Statistics
    total_profit = Column(DECIMAL(20, 8), default=0)
    total_profit_percent = Column(DECIMAL(10, 4), default=0)
    completed_cycles = Column(Integer, default=0)
    total_buy_orders = Column(Integer, default=0)
    total_sell_orders = Column(Integer, default=0)

    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.now())
    started_at = Column(TIMESTAMP, nullable=True)
    stopped_at = Column(TIMESTAMP, nullable=True)
    last_activity_at = Column(TIMESTAMP, nullable=True)

    # Relationships
    user = relationship("User", back_populates="grid_bots")
    orders = relationship("GridOrder", back_populates="grid_bot", cascade="all, delete-orphan")
    logs = relationship("BotLog", back_populates="grid_bot", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<GridBot(id={self.id}, symbol={self.symbol}, status={self.status})>"

    @property
    def is_active(self) -> bool:
        """Check if bot is active."""
        return self.status == "active"

    @property
    def display_name(self) -> str:
        """Get display name for the bot."""
        return self.bot_name or f"Grid Bot #{self.id} - {self.symbol}"

    @property
    def price_range_percent(self) -> float:
        """Calculate price range as percentage."""
        if self.lower_price and self.upper_price and self.lower_price > 0:
            return float((self.upper_price - self.lower_price) / self.lower_price * 100)
        return 0.0

    @property
    def grid_step(self) -> float:
        """Calculate grid step size."""
        if self.grid_levels and self.lower_price and self.upper_price:
            return float((self.upper_price - self.lower_price) / self.grid_levels)
        return 0.0
