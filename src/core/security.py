"""Security utilities for encryption and decryption."""
from cryptography.fernet import Fernet
from src.core.config import settings
import logging

logger = logging.getLogger(__name__)


class SecurityManager:
    """Manager for encryption and decryption operations."""

    def __init__(self):
        """Initialize security manager."""
        if not settings.ENCRYPTION_KEY:
            raise ValueError("ENCRYPTION_KEY must be set in environment variables")

        try:
            self.cipher = Fernet(settings.ENCRYPTION_KEY.encode())
        except Exception as e:
            logger.error(f"Failed to initialize cipher: {e}")
            raise ValueError(f"Invalid ENCRYPTION_KEY: {e}")

    def encrypt(self, data: str) -> str:
        """
        Encrypt string data.

        Args:
            data: String to encrypt

        Returns:
            Encrypted string
        """
        if not data:
            return ""

        try:
            encrypted = self.cipher.encrypt(data.encode())
            return encrypted.decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise

    def decrypt(self, encrypted_data: str) -> str:
        """
        Decrypt string data.

        Args:
            encrypted_data: Encrypted string

        Returns:
            Decrypted string
        """
        if not encrypted_data:
            return ""

        try:
            decrypted = self.cipher.decrypt(encrypted_data.encode())
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise

    def encrypt_api_credentials(self, api_key: str, api_secret: str) -> tuple[str, str]:
        """
        Encrypt API credentials.

        Args:
            api_key: API key
            api_secret: API secret

        Returns:
            Tuple of (encrypted_api_key, encrypted_api_secret)
        """
        return self.encrypt(api_key), self.encrypt(api_secret)

    def decrypt_api_credentials(self, encrypted_api_key: str, encrypted_api_secret: str) -> tuple[str, str]:
        """
        Decrypt API credentials.

        Args:
            encrypted_api_key: Encrypted API key
            encrypted_api_secret: Encrypted API secret

        Returns:
            Tuple of (api_key, api_secret)
        """
        return self.decrypt(encrypted_api_key), self.decrypt(encrypted_api_secret)


# Global instance
security = SecurityManager() if settings.ENCRYPTION_KEY else None


def generate_encryption_key() -> str:
    """Generate a new Fernet encryption key."""
    return Fernet.generate_key().decode()


if __name__ == "__main__":
    # Generate new encryption key
    print(f"New encryption key: {generate_encryption_key()}")
