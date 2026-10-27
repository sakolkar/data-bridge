class DatabridgeException(Exception):
    """
    Base class for all DataBridge application exceptions.
    """


class ConfigurationError(DatabridgeException, ValueError):
    """
    Application configuration is invalid or cannot be prepared.
    """


class ConnectionStoreError(DatabridgeException):
    """
    A caller-safe persistence or encryption failure.
    """


class DuplicateConnectionError(ConnectionStoreError):
    """
    The connection name is already in use.
    """


class ConnectionNotFoundError(ConnectionStoreError):
    """
    No connection exists with the given name.
    """


class TransferStoreError(DatabridgeException):
    """
    A transfer persistence operation failed.
    """


class TransferNotFoundError(TransferStoreError):
    """
    No transfer exists with the given identifier.
    """


class ConnectorError(DatabridgeException):
    """
    A connector operation failed; messages must be safe for callers.
    """


class ConnectorFileNotFoundError(ConnectorError):
    """
    The requested file does not exist.
    """


class ConnectorFileExistsError(ConnectorError):
    """
    Creating a file would overwrite an existing entry.
    """


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
