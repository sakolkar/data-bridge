class DatabridgeException(Exception):
    """
    Base class for all DataBridge application exceptions.
    """

    default_message = "DataBridge operation failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class ConfigurationError(DatabridgeException, ValueError):
    """
    Application configuration is invalid or cannot be prepared.
    """


class ConnectionStoreError(DatabridgeException):
    """
    A caller-safe persistence or encryption failure.
    """

    default_message = "Connection storage is unavailable."


class DuplicateConnectionError(ConnectionStoreError):
    """
    The connection name is already in use.
    """

    default_message = "Connection already exists."


class ConnectionNotFoundError(ConnectionStoreError):
    """
    No connection exists with the given name.
    """

    default_message = "Connection not found."


class TransferStoreError(DatabridgeException):
    """
    A transfer persistence operation failed.
    """

    default_message = "Transfer storage is unavailable."


class TransferNotFoundError(TransferStoreError):
    """
    No transfer exists with the given identifier.
    """

    default_message = "Transfer not found."


class ConnectorError(DatabridgeException):
    """
    A connector operation failed; messages must be safe for callers.
    """

    default_message = "File operation failed."


class ConnectorFileNotFoundError(ConnectorError):
    """
    The requested file does not exist.
    """

    default_message = "File not found."


class ConnectorFileExistsError(ConnectorError):
    """
    Creating a file would overwrite an existing entry.
    """

    default_message = "File already exists."


class InvalidConnectorArgumentError(ConnectorError):
    """
    A logical path or operation argument is invalid.
    """


class ConnectorOperationError(ConnectorError):
    """
    A storage, permission, authentication, or connectivity failure.
    """


class ConnectorPermissionError(ConnectorOperationError):
    """
    The storage backend denied access to the requested path.
    """


class ConnectorUnavailableError(ConnectorOperationError):
    """
    The backend could not be reached or rejected the configured credentials.
    """
