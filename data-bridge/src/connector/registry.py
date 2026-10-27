from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel
from pydantic.fields import FieldInfo

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


class _ConnectionType(BaseModel):
    type: Literal[tuple(_CONNECTORS)]  # type: ignore[valid-type]


def validate_configuration(body: dict[str, object]) -> BaseModel:
    # Validate the type first so unsupported types list the supported ones.
    connection_type = _ConnectionType.model_validate(body).type
    config_model, _ = _CONNECTORS[connection_type]
    return config_model.model_validate(body)


def _is_secret(field: FieldInfo) -> bool:
    extra = field.json_schema_extra
    return isinstance(extra, dict) and extra.get("writeOnly") is True


def public_settings(connection_type: str, settings: dict[str, object]) -> dict[str, object]:
    """
    Return stored settings without write-only fields such as passwords.
    """
    connector = _CONNECTORS.get(connection_type)
    if connector is None:
        return {}
    config_model, _ = connector
    secrets = {name for name, field in config_model.model_fields.items() if _is_secret(field)}
    return {key: value for key, value in settings.items() if key not in secrets}


def create_connector(connection_type: str, settings: dict[str, object]) -> Connector:
    connector = _CONNECTORS.get(connection_type)
    if connector is None:
        raise InvalidConnectorArgumentError("Unsupported connection type.")
    _, factory = connector
    return factory(settings)
