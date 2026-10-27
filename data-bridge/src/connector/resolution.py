import json

from pydantic import ValidationError

from connection_store import ConnectionStore
from connector import Connector
from connector.registry import create_connector
from exceptions import ConnectorOperationError


def resolve_connector(store: ConnectionStore, name: str) -> Connector:
    record = store.get(name)
    try:
        settings = json.loads(record.settings)
        if not isinstance(settings, dict):
            raise ConnectorOperationError("Stored connection configuration is invalid.")
        return create_connector(record.type, settings)
    except (json.JSONDecodeError, ValidationError):
        raise ConnectorOperationError("Stored connection configuration is invalid.") from None
