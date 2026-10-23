from collections.abc import Callable

from connector import Connector
from connector.local import LocalConnectionSettings, LocalConnector
from exceptions import InvalidConnectorArgumentError

ConnectorFactory = Callable[[dict[str, object]], Connector]


def _create_local(settings: dict[str, object]) -> Connector:
    configuration = LocalConnectionSettings.model_validate(settings)
    return LocalConnector(configuration.path)


_CONNECTOR_FACTORIES: dict[str, ConnectorFactory] = {
    "local": _create_local,
}


def create_connector(connection_type: str, settings: dict[str, object]) -> Connector:
    factory = _CONNECTOR_FACTORIES.get(connection_type)
    if factory is None:
        raise InvalidConnectorArgumentError("Unsupported connection type.")
    return factory(settings)
