"""GridOrder model."""
from sqlalchemy import Column, Integer, String, DECIMAL, TIMESTAMP, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from src.core.database import Base


class GridOrder(Base):
    """GridOrder model for storing grid trading orders."""

    __tablename__ = "grid_orders"

    id = Column(Integer, primary_key=True, index=True)
    grid_bot_id = Column(Integer, ForeignKey("grid_bots.id", ondelete="CASCADE"), nullable=False, index=True)

    # Exchange order details
    exchange_order_id = Column(String(255), unique=True, nullable=True, index=True)

    # Order details
    side = Column(String(10), nullable=False)  # 'buy' or 'sell'
    order_type = Column(String(20), default="limit")
    level = Column(Integer, nullable=False)  # Grid level (0 to N)
    price = Column(DECIMAL(20, 8), nullable=False)
    amount = Column(DECIMAL(20, 8), nullable=False)
    total = Column(DECIMAL(20, 8), nullable=True)  # price * amount

    # Status: open, filled, cancelled, error
    status = Column(String(20), default="open", index=True)

    # Fees
    fee = Column(DECIMAL(20, 8), default=0)
    fee_currency = Column(String(20), nullable=True)

    # Pair tracking (для расчета прибыли)
    paired_order_id = Column(Integer, ForeignKey("grid_orders.id"), nullable=True)
    profit = Column(DECIMAL(20, 8), nullable=True)

    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.now())
    filled_at = Column(TIMESTAMP, nullable=True)
    cancelled_at = Column(TIMESTAMP, nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    # Relationships
    grid_bot = relationship("GridBot", back_populates="orders")
    paired_order = relationship("GridOrder", remote_side=[id], uselist=False)

    def __repr__(self):
        return f"<GridOrder(id={self.id}, side={self.side}, price={self.price}, status={self.status})>"

    @property
    def is_open(self) -> bool:
        """Check if order is open."""
        return self.status == "open"

    @property
    def is_filled(self) -> bool:
        """Check if order is filled."""
        return self.status == "filled"

    @property
    def is_buy(self) -> bool:
        """Check if order is buy."""
        return self.side == "buy"

    @property
    def is_sell(self) -> bool:
        """Check if order is sell."""
        return self.side == "sell"

    @property
    def total_cost(self) -> float:
        """Calculate total cost including fees."""
        base_total = float(self.total or (self.price * self.amount))
        fee_cost = float(self.fee or 0)
        return base_total + fee_cost
