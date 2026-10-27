import json

from pydantic import ValidationError

from controllers.connection import ConnectionController
from connector import Connector
from connector.registry import create_connector
from exceptions import ConnectorOperationError


def resolve_connector(controller: ConnectionController, name: str) -> Connector:
    record = controller.get(name)
    try:
        settings = json.loads(record.settings)
        if not isinstance(settings, dict):
            raise ConnectorOperationError("Stored connection configuration is invalid.")
        return create_connector(record.type, settings)
    except (json.JSONDecodeError, ValidationError):
        raise ConnectorOperationError("Stored connection configuration is invalid.") from None
