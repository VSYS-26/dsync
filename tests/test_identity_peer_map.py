from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dsync.identity import PeerMapStore


class _Clock:
    def __init__(self, start: int) -> None:
        self.current = start

    def now(self) -> float:
        return float(self.current)


class PeerMapStoreTests(unittest.TestCase):
    def test_upsert_overwrites_existing_fingerprint(self) -> None:
        clock = _Clock(100)
        with tempfile.TemporaryDirectory() as tmp_dir:
            map_path = Path(tmp_dir) / ".dsync" / "peer-map.json"
            store = PeerMapStore(file_path=map_path, ttl_seconds=30, now_fn=clock.now)

            store.upsert_peer("hex-abc", "192.168.1.10")
            clock.current = 110
            store.upsert_peer("hex-abc", "192.168.1.99")

            peers = store.list_peers()
            self.assertEqual(len(peers), 1)
            self.assertEqual(peers["hex-abc"].ipv4, "192.168.1.99")
            self.assertEqual(peers["hex-abc"].last_seen, 110)
            self.assertEqual(peers["hex-abc"].expires_at, 140)

    def test_purge_expired_removes_old_entries(self) -> None:
        clock = _Clock(200)
        with tempfile.TemporaryDirectory() as tmp_dir:
            map_path = Path(tmp_dir) / ".dsync" / "peer-map.json"
            store = PeerMapStore(file_path=map_path, ttl_seconds=30, now_fn=clock.now)

            store.upsert_peer("hex-abc", "10.0.0.2")
            clock.current = 231

            peers = store.purge_expired()
            self.assertEqual(peers, {})

    def test_invalid_ipv4_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            map_path = Path(tmp_dir) / ".dsync" / "peer-map.json"
            store = PeerMapStore(file_path=map_path)

            with self.assertRaises(ValueError):
                store.upsert_peer("hex-abc", "not-an-ip")


if __name__ == "__main__":
    unittest.main()

