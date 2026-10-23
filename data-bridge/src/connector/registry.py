from collections.abc import Callable

from pydantic import BaseModel

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
from exceptions import InvalidConnectorArgumentError

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


def validate_configuration(body: dict[str, object]) -> BaseModel:
    connection_type = body.get("type")
    config_model, _ = _CONNECTORS.get(
        connection_type if isinstance(connection_type, str) else "",
        _CONNECTORS["local"],
    )
    return config_model.model_validate(body)


def create_connector(connection_type: str, settings: dict[str, object]) -> Connector:
    connector = _CONNECTORS.get(connection_type)
    if connector is None:
        raise InvalidConnectorArgumentError("Unsupported connection type.")
    _, factory = connector
    return factory(settings)
