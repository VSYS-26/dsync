from __future__ import annotations

import ipaddress
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

DEFAULT_PEER_MAP_PATH = Path(".dsync") / "peer-map.json"
DEFAULT_TTL_SECONDS = 30


@dataclass(slots=True)
class DiscoveredPeer:
	"""Represents one discovered peer entry in the local mapping."""

	fingerprint: str
	ipv4: str
	last_seen: int
	expires_at: int


class PeerMapStore:
	"""Persists fingerprint-to-IPv4 mappings in a local JSON file."""

	def __init__(
		self,
		file_path: str | Path = DEFAULT_PEER_MAP_PATH,
		ttl_seconds: int = DEFAULT_TTL_SECONDS,
		now_fn: Callable[[], float] | None = None,
	) -> None:
		self.file_path = Path(file_path)
		self.ttl_seconds = ttl_seconds
		self._now_fn = now_fn if now_fn is not None else time.time

	def list_peers(self) -> dict[str, DiscoveredPeer]:
		"""Return active peers after removing expired entries."""
		peers = self._load_peers()
		updated = self._purge_expired(peers)
		if updated:
			self._save_peers(peers)
		return peers

	def upsert_peer(self, fingerprint: str, ipv4: str) -> DiscoveredPeer:
		"""Update or insert a peer entry with last-write-wins semantics."""
		normalized_ip = str(ipaddress.IPv4Address(ipv4))
		if not fingerprint:
			raise ValueError("Fingerprint must not be empty")

		peers = self._load_peers()
		updated = self._purge_expired(peers)
		now = int(self._now_fn())
		peer = DiscoveredPeer(
			fingerprint=fingerprint,
			ipv4=normalized_ip,
			last_seen=now,
			expires_at=now + self.ttl_seconds,
		)
		peers[fingerprint] = peer
		self._save_peers(peers)
		return peer

	def purge_expired(self) -> dict[str, DiscoveredPeer]:
		"""Remove expired entries and return the remaining peer map."""
		peers = self._load_peers()
		updated = self._purge_expired(peers)
		if updated:
			self._save_peers(peers)
		return peers

	def _load_peers(self) -> dict[str, DiscoveredPeer]:
		if not self.file_path.exists():
			return {}

		raw = json.loads(self.file_path.read_text(encoding="utf-8"))
		raw_peers = raw.get("peers", {}) if isinstance(raw, dict) else {}

		peers: dict[str, DiscoveredPeer] = {}
		for fingerprint, value in raw_peers.items():
			if not isinstance(fingerprint, str) or not isinstance(value, dict):
				continue
			ipv4 = value.get("ipv4")
			last_seen = value.get("last_seen")
			expires_at = value.get("expires_at")
			if (
				not isinstance(ipv4, str)
				or not isinstance(last_seen, int)
				or not isinstance(expires_at, int)
			):
				continue
			try:
				normalized_ip = str(ipaddress.IPv4Address(ipv4))
			except ipaddress.AddressValueError:
				continue
			peers[fingerprint] = DiscoveredPeer(
				fingerprint=fingerprint,
				ipv4=normalized_ip,
				last_seen=last_seen,
				expires_at=expires_at,
			)
		return peers

	def _save_peers(self, peers: dict[str, DiscoveredPeer]) -> None:
		self.file_path.parent.mkdir(parents=True, exist_ok=True)
		payload = {
			"meta": {
				"ttl_seconds": self.ttl_seconds,
				"updated_at": int(self._now_fn()),
			},
			"peers": {fingerprint: asdict(peer) for fingerprint, peer in peers.items()},
		}
		json_data = json.dumps(payload, indent=2, sort_keys=True)

		with NamedTemporaryFile(
			"w",
			encoding="utf-8",
			dir=self.file_path.parent,
			delete=False,
		) as tmp:
			tmp_path = Path(tmp.name)
			tmp.write(json_data)
			tmp.write("\n")
		tmp_path.replace(self.file_path)

	def _purge_expired(self, peers: dict[str, DiscoveredPeer]) -> bool:
		now = int(self._now_fn())
		expired = [fingerprint for fingerprint, peer in peers.items() if peer.expires_at <= now]
		for fingerprint in expired:
			del peers[fingerprint]
		return len(expired) > 0


def peer_map_path(project_root: str | Path = ".") -> Path:
	"""Return the default project-local peer map path."""
	return Path(project_root) / DEFAULT_PEER_MAP_PATH

