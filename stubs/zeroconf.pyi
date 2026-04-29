from typing import Any, Protocol

class IPVersion:
    V4Only: IPVersion

class ServiceInfo:
    type_: str
    name: str
    addresses: list[bytes]
    properties: dict[bytes, Any]
    server: str
    port: int

    def __init__(
        self,
        *,
        type_: str | None = None,
        name: str | None = None,
        addresses: list[bytes] | None = None,
        port: int = 0,
        properties: dict[bytes, Any] | None = None,
        server: str | None = None,
    ) -> None: ...
    def parsed_addresses(self, version: IPVersion) -> list[str]: ...

class ServiceListener(Protocol):
    def add_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None: ...
    def update_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None: ...
    def remove_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None: ...

class Zeroconf:
    def __init__(self, *, ip_version: IPVersion | None = None) -> None: ...
    def register_service(self, info: ServiceInfo) -> None: ...
    def unregister_service(self, info: ServiceInfo) -> None: ...
    def get_service_info(self, type_: str, name: str, timeout: int = ...) -> ServiceInfo | None: ...
    def close(self) -> None: ...

class ServiceBrowser:
    def __init__(self, zeroconf: Zeroconf, type_: str, listener: ServiceListener) -> None: ...
    def cancel(self) -> None: ...
