import json
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ValidationError

from connector import Connector
from connector.local import (
    LocalConnectionConfiguration,
    LocalConnectionSettings,
    LocalConnector,
)
from connector.sftp import (
    SFTPConnectionConfiguration,
    SFTPConnectionSettings,
    SFTPConnector,
)
from controllers.connection import ConnectionController
from exceptions import ConnectorOperationError, InvalidConnectorArgumentError

ConnectorFactory = Callable[[dict[str, object]], Connector]
Configuration = type[BaseModel]


def _create_local(settings: dict[str, object]) -> Connector:
    configuration = LocalConnectionSettings.model_validate(settings)
    return LocalConnector(configuration.path)


def _create_sftp(settings: dict[str, object]) -> Connector:
    configuration = SFTPConnectionSettings.model_validate(settings)
    return SFTPConnector(
        configuration.host,
        configuration.port,
        configuration.username,
        configuration.password,
    )


_CONNECTORS: dict[str, tuple[Configuration, ConnectorFactory]] = {
    "local": (LocalConnectionConfiguration, _create_local),
    "sftp": (SFTPConnectionConfiguration, _create_sftp),
}


class _ConnectionType(BaseModel):
    type: Literal[tuple(_CONNECTORS)]  # type: ignore[valid-type]


def validate_configuration(body: dict[str, object]) -> BaseModel:
    # Validate the type first so unsupported types list the supported ones.
    connection_type = _ConnectionType.model_validate(body).type
    config_model, _ = _CONNECTORS[connection_type]
    return config_model.model_validate(body)


def resolve_connector(controller: ConnectionController, name: str) -> Connector:
    record = controller.get(name)
    connector = _CONNECTORS.get(record.type)
    if connector is None:
        raise InvalidConnectorArgumentError("Unsupported connection type.")
    _, factory = connector
    try:
        settings = json.loads(record.settings)
        if not isinstance(settings, dict):
            raise ConnectorOperationError("Stored connection configuration is invalid.")
        return factory(settings)
    except (json.JSONDecodeError, ValidationError):
        raise ConnectorOperationError("Stored connection configuration is invalid.") from None
