from sqlalchemy.types import TypeDecorator, String
from cryptography.fernet import Fernet
from config import load_settings

settings = load_settings()


class EncryptedString(TypeDecorator):
    """
    Transparently encrypts a String column on write and decrypts on read.
    Stores the data as a string representing the encrypted token.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """
        Encrypt the data before saving it to the database.
        """
        if value is not None:
            fernet = Fernet(settings.secret_key)
            encrypted_bytes = fernet.encrypt(value.encode("utf-8"))
            return encrypted_bytes.decode("utf-8")
        return value

    def process_result_value(self, value, dialect):
        """
        Decrypt the data when loading it from the database.
        """
        if value is not None:
            try:
                fernet = Fernet(settings.secret_key)
                decrypted_bytes = fernet.decrypt(value.encode("utf-8"))
                return decrypted_bytes.decode("utf-8")
            except Exception:
                return "[DECRYPTION_FAILED]"
        return value
