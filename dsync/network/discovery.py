from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import cast

from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

from dsync.identity import DiscoveredPeer, PeerMapStore

SERVICE_TYPE = "_dsync-peer._tcp.local."
DEFAULT_DISCOVERY_SECONDS = 10


@dataclass(slots=True)
class DiscoveryStats:
    """Small runtime stats that can be shown in the CLI."""

    events_seen: int = 0
    peers_written: int = 0


class _PeerListener(ServiceListener):
    def __init__(
        self,
        zeroconf: Zeroconf,
        store: PeerMapStore,
        stats: DiscoveryStats,
        own_fingerprint: str | None,
    ) -> None:
        self._zeroconf = zeroconf
        self._store = store
        self._stats = stats
        self._own_fingerprint = own_fingerprint

    def add_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        self._handle_event(service_type, name)

    def update_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        self._handle_event(service_type, name)

    def remove_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        self._store.purge_expired()

    def _handle_event(self, service_type: str, name: str) -> None:
        info = self._zeroconf.get_service_info(service_type, name, timeout=1000)
        if info is None:
            self._store.purge_expired()
            return

        self._stats.events_seen += 1
        self._store.purge_expired()

        fingerprint = _read_fingerprint(info)
        if not fingerprint or fingerprint == self._own_fingerprint:
            return

        ipv4 = _extract_ipv4(info)
        if ipv4 is None:
            return

        self._store.upsert_peer(fingerprint=fingerprint, ipv4=ipv4)
        self._stats.peers_written += 1


def _read_fingerprint(info: ServiceInfo) -> str:
    raw_value = info.properties.get(b"fingerprint")
    if raw_value is None:
        return ""
    if isinstance(raw_value, bytes):
        return raw_value.decode("utf-8", errors="ignore").strip()
    if isinstance(raw_value, str):
        return raw_value.strip()
    return ""


def _extract_ipv4(info: ServiceInfo) -> str | None:
    if hasattr(info, "parsed_addresses"):
        parsed = cast(list[str], info.parsed_addresses(version=IPVersion.V4Only))
        for value in parsed:
            try:
                return str(socket.inet_ntoa(socket.inet_aton(value)))
            except OSError:
                continue

    for raw_addr in info.addresses:
        if len(raw_addr) == 4:
            try:
                return socket.inet_ntoa(raw_addr)
            except OSError:
                continue
    return None


def _local_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)


class FingerprintAnnouncer:
    """Announces the local fingerprint in the LAN via mDNS/Zeroconf."""

    def __init__(self, fingerprint: str, service_port: int = 0) -> None:
        self._fingerprint = fingerprint
        self._service_port = service_port
        self._zeroconf: Zeroconf | None = None
        self._service_info: ServiceInfo | None = None

    def start(self) -> None:
        if self._zeroconf is not None:
            return

        self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        host = socket.gethostname()
        service_name = f"{host}-{self._fingerprint[:8]}.{SERVICE_TYPE}"
        address = socket.inet_aton(_local_ipv4())
        self._service_info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=service_name,
            addresses=[address],
            port=self._service_port,
            properties={b"fingerprint": self._fingerprint.encode("utf-8")},
            server=f"{host}.local.",
        )
        self._zeroconf.register_service(self._service_info)

    def stop(self) -> None:
        if self._zeroconf is None:
            return

        if self._service_info is not None:
            self._zeroconf.unregister_service(self._service_info)
        self._zeroconf.close()
        self._zeroconf = None
        self._service_info = None


class PeerDiscoveryRunner:
    """Runs a short discovery window and writes events into the peer map store."""

    def __init__(self, store: PeerMapStore) -> None:
        self._store = store

    def discover(
        self,
        duration_seconds: int = DEFAULT_DISCOVERY_SECONDS,
        own_fingerprint: str | None = None,
    ) -> tuple[dict[str, DiscoveredPeer], DiscoveryStats]:
        stats = DiscoveryStats()
        zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        listener = _PeerListener(
            zeroconf=zeroconf,
            store=self._store,
            stats=stats,
            own_fingerprint=own_fingerprint,
        )
        browser = ServiceBrowser(zeroconf, SERVICE_TYPE, listener)

        end = time.monotonic() + max(duration_seconds, 0)
        try:
            while time.monotonic() < end:
                time.sleep(0.2)
        finally:
            self._store.purge_expired()
            browser.cancel()
            zeroconf.close()

        return self._store.list_peers(), stats

