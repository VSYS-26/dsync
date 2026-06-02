import json
from pathlib import Path

import pytest

from dsync.identity import DiscoveredPeer, PeerMapStore, peer_map_path


def _store(tmp_path: Path, now: float = 1000.0, ttl: int = 30) -> PeerMapStore:
    return PeerMapStore(
        file_path=tmp_path / "peer-map.json",
        ttl_seconds=ttl,
        now_fn=lambda: now,
    )


def test_upsert_and_list_peers(tmp_path: Path) -> None:
    store = _store(tmp_path, now=1000.0)
    store.upsert_peer("fp-aaa", "192.168.1.1")
    peers = store.list_peers()
    assert "fp-aaa" in peers
    assert peers["fp-aaa"].ipv4 == "192.168.1.1"


def test_expired_peer_excluded(tmp_path: Path) -> None:
    store = _store(tmp_path, now=1000.0, ttl=30)
    store.upsert_peer("fp-expired", "10.0.0.1")
    store_later = _store(tmp_path, now=1031.0, ttl=30)
    peers = store_later.list_peers()
    assert "fp-expired" not in peers


def test_fresh_peer_not_excluded(tmp_path: Path) -> None:
    store = _store(tmp_path, now=1000.0, ttl=30)
    store.upsert_peer("fp-fresh", "10.0.0.2")
    store_later = _store(tmp_path, now=1015.0, ttl=30)
    peers = store_later.list_peers()
    assert "fp-fresh" in peers


def test_purge_expired_removes_old(tmp_path: Path) -> None:
    _store(tmp_path, now=1000.0, ttl=30).upsert_peer("fp-old", "10.0.0.1")  # expires 1030
    _store(tmp_path, now=1010.0, ttl=30).upsert_peer("fp-new", "10.0.0.2")  # expires 1040

    # at t=1031: fp-old expired, fp-new still valid
    remaining = _store(tmp_path, now=1031.0, ttl=30).purge_expired()
    assert "fp-old" not in remaining
    assert "fp-new" in remaining


def test_ttl_applied_on_upsert(tmp_path: Path) -> None:
    store = _store(tmp_path, now=1000.0, ttl=60)
    peer = store.upsert_peer("fp-ttl", "192.168.0.1")
    assert peer.expires_at == 1000 + 60
    assert peer.last_seen == 1000


def test_upsert_updates_existing_peer(tmp_path: Path) -> None:
    store = _store(tmp_path, now=1000.0)
    store.upsert_peer("fp-x", "10.0.0.1")
    store_later = _store(tmp_path, now=1010.0)
    store_later.upsert_peer("fp-x", "10.0.0.99")
    peers = store_later.list_peers()
    assert peers["fp-x"].ipv4 == "10.0.0.99"
    assert peers["fp-x"].last_seen == 1010


def test_round_trip_persistence(tmp_path: Path) -> None:
    store = _store(tmp_path, now=1000.0, ttl=300)
    store.upsert_peer("fp-persist", "172.16.0.1")
    store2 = _store(tmp_path, now=1001.0, ttl=300)
    peers = store2.list_peers()
    assert "fp-persist" in peers
    assert peers["fp-persist"].ipv4 == "172.16.0.1"


def test_empty_fingerprint_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="must not be empty"):
        store.upsert_peer("", "10.0.0.1")


def test_invalid_ipv4_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="octets"):
        store.upsert_peer("fp-bad", "not-an-ip")


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.list_peers() == {}


def test_discovered_peer_is_dataclass() -> None:
    peer = DiscoveredPeer(fingerprint="fp", ipv4="1.2.3.4", last_seen=100, expires_at=130)
    assert peer.fingerprint == "fp"
    assert peer.ipv4 == "1.2.3.4"


def test_load_skips_non_dict_peer_value(tmp_path: Path) -> None:
    map_file = tmp_path / "peer-map.json"
    map_file.write_text(
        json.dumps(
            {
                "peers": {
                    "fp-bad": "not-a-dict",
                    "fp-good": {"ipv4": "1.2.3.4", "last_seen": 1000, "expires_at": 9999},
                }
            }
        )
    )
    store = PeerMapStore(file_path=map_file, ttl_seconds=3600, now_fn=lambda: 1000.0)
    peers = store._load_peers()
    assert "fp-bad" not in peers
    assert "fp-good" in peers


def test_load_skips_entry_missing_fields(tmp_path: Path) -> None:
    map_file = tmp_path / "peer-map.json"
    map_file.write_text(json.dumps({"peers": {"fp-missing": {"ipv4": "1.2.3.4"}}}))
    store = PeerMapStore(file_path=map_file, ttl_seconds=3600, now_fn=lambda: 1000.0)
    peers = store._load_peers()
    assert "fp-missing" not in peers


def test_load_skips_entry_with_invalid_ipv4(tmp_path: Path) -> None:
    map_file = tmp_path / "peer-map.json"
    map_file.write_text(
        json.dumps(
            {"peers": {"fp-badip": {"ipv4": "not-an-ip", "last_seen": 1000, "expires_at": 9999}}}
        )
    )
    store = PeerMapStore(file_path=map_file, ttl_seconds=3600, now_fn=lambda: 1000.0)
    peers = store._load_peers()
    assert "fp-badip" not in peers


def test_peer_map_path_default() -> None:
    path = peer_map_path(".")
    assert path.name == "peer-map.json"
    assert ".dsync" in str(path)
