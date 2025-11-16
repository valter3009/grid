"""Main application entry point."""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import init_db, close_db, AsyncSessionLocal
from src.services.mexc_service import MEXCService
from src.services.grid_strategy import GridStrategy
from src.services.bot_manager import BotManager
from src.services.order_monitor import OrderMonitor
from src.services.notification import NotificationService
from src.services.health_check import HealthCheck

from src.bot.handlers import start, api_setup, balance, manage_bots, create_bot

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(settings.LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


# Dependency injection for database session
async def get_db_session() -> AsyncSession:
    """Get database session."""
    async with AsyncSessionLocal() as session:
        yield session


class Application:
    """Main application class."""

    def __init__(self):
        """Initialize application."""
        self.bot = None
        self.dp = None
        self.mexc_service = None
        self.grid_strategy = None
        self.bot_manager = None
        self.order_monitor = None
        self.notification_service = None
        self.health_check = None

    async def setup(self):
        """Setup application components."""
        logger.info("Setting up application...")

        # Initialize database
        await init_db()
        logger.info("Database initialized")

        # Initialize Telegram bot
        self.bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        self.dp = Dispatcher(storage=MemoryStorage())

        # Register middleware for DB session injection
        @self.dp.message.middleware()
        @self.dp.callback_query.middleware()
        async def db_session_middleware(handler, event, data):
            """Inject DB session into handlers."""
            async with AsyncSessionLocal() as session:
                data['db'] = session
                return await handler(event, data)

        # Initialize services (they will be created per-request with DB session)
        self.notification_service = NotificationService(self.bot)
        logger.info("Services initialized")

        # Register handlers
        self.dp.include_router(start.router)
        self.dp.include_router(api_setup.router)
        self.dp.include_router(balance.router)
        self.dp.include_router(manage_bots.router)
        self.dp.include_router(create_bot.router)
        logger.info("Handlers registered")

        # Restore active bots
        async with AsyncSessionLocal() as session:
            mexc_service = MEXCService(session)
            grid_strategy = GridStrategy(session, mexc_service)
            bot_manager = BotManager(session, mexc_service, grid_strategy)

            restored = await bot_manager.restore_bots_after_restart()
            logger.info(f"Restored {restored} active bots")

            # Initialize OrderMonitor and start monitoring for restored bots
            self.order_monitor = OrderMonitor(
                db_factory=lambda: AsyncSessionLocal(),
                mexc_service=mexc_service,
                grid_strategy=grid_strategy,
                notification_service=self.notification_service
            )

            # Start monitoring for all active bots
            from sqlalchemy import select
            from src.models.grid_bot import GridBot

            result = await session.execute(
                select(GridBot).where(GridBot.status == 'active')
            )
            active_bots = result.scalars().all()

            for bot in active_bots:
                self.order_monitor.start_monitoring(bot.id)
                logger.info(f"Started monitoring for bot {bot.id}")

            # Start health check service
            self.health_check = HealthCheck(
                db=session,
                mexc_service=mexc_service,
                grid_strategy=grid_strategy,
                notification_service=self.notification_service
            )

            # Start periodic health check in background
            asyncio.create_task(
                self.health_check.run_periodic_health_check(
                    lambda: AsyncSessionLocal()
                )
            )
            logger.info("Health check service started")

    async def start(self):
        """Start the application."""
        logger.info("Starting bot...")
        try:
            await self.setup()

            # Start polling
            logger.info("Bot is running...")
            await self.dp.start_polling(self.bot)

        except Exception as e:
            logger.error(f"Error starting bot: {e}")
            raise
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Shutdown application gracefully."""
        logger.info("Shutting down...")

        # Stop order monitoring
        if self.order_monitor:
            await self.order_monitor.stop_all()
            logger.info("Order monitoring stopped")

        # Close MEXC connections
        if self.mexc_service:
            await self.mexc_service.close_all()
            logger.info("MEXC connections closed")

        # Close database
        await close_db()
        logger.info("Database closed")

        # Close bot
        if self.bot:
            await self.bot.session.close()
            logger.info("Bot session closed")

        logger.info("Shutdown complete")


def main():
    """Main entry point."""
    try:
        # Check if bot token is configured
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN is not set in environment variables")
            sys.exit(1)

        # Check if encryption key is configured
        if not settings.ENCRYPTION_KEY:
            logger.error("ENCRYPTION_KEY is not set. Generate one with:")
            logger.error("python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")
            sys.exit(1)

        # Create and run application
        app = Application()
        asyncio.run(app.start())

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
