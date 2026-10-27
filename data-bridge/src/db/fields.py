from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import String, TypeDecorator

from exceptions import ConnectionStoreError

_fernet: Fernet | None = None


def configure(secret_key: str) -> None:
    global _fernet
    _fernet = Fernet(secret_key)


def _get_fernet() -> Fernet:
    if _fernet is None:
        raise ConnectionStoreError("Encryption is not configured.")
    return _fernet


class EncryptedString(TypeDecorator):
    """A String column stored as a Fernet token."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return _get_fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            raise ConnectionStoreError("Cannot decrypt stored connection settings.") from None
