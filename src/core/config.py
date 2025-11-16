"""Application configuration."""
import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Settings:
    """Application settings."""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    # Database
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "crypto_grid_bot")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")

    # Redis
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))

    # Security
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")

    # Monitoring
    ORDER_CHECK_INTERVAL: int = int(os.getenv("ORDER_CHECK_INTERVAL", "10"))
    HEALTH_CHECK_INTERVAL: int = int(os.getenv("HEALTH_CHECK_INTERVAL", "300"))

    # Limits
    MAX_GRID_LEVELS: int = int(os.getenv("MAX_GRID_LEVELS", "50"))
    MIN_GRID_LEVELS: int = int(os.getenv("MIN_GRID_LEVELS", "3"))
    MIN_INVESTMENT_USDT: float = float(os.getenv("MIN_INVESTMENT_USDT", "50"))

    # Notifications
    PROFIT_NOTIFY_PERCENT: float = float(os.getenv("PROFIT_NOTIFY_PERCENT", "5.0"))
    DAILY_SUMMARY_TIME: str = os.getenv("DAILY_SUMMARY_TIME", "09:00")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "logs/bot.log")

    @property
    def database_url(self) -> str:
        """Get database URL."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def db_echo(self) -> bool:
        """Echo SQL queries (for debugging)."""
        return self.LOG_LEVEL == "DEBUG"

    @property
    def redis_url(self) -> str:
        """Get Redis URL."""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"


settings = Settings()
