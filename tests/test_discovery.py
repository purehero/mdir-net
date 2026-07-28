# 피어 발견 — 인터페이스마다 알림을 보내는지, 받은 알림을 어떻게 다루는지 확인

import socket
import threading
import time
import unittest

from mdirnet import config, discovery


class TestLocalIps(unittest.TestCase):
    def test_loopback_is_not_a_candidate(self):
        """127.x 로 브로드캐스트를 보내도 LAN 에는 아무도 못 듣는다."""
        for ip in discovery.local_ips():
            self.assertFalse(ip.startswith("127."), ip)

    def test_no_duplicates(self):
        ips = discovery.local_ips()
        self.assertEqual(len(ips), len(set(ips)), ips)

    def test_routing_address_comes_first(self):
        """라우팅 테이블이 고른 주소가 실제로 나가는 길이다. 먼저 시도한다."""
        primary = discovery.local_ip()
        if primary.startswith("127."):
            self.skipTest("네트워크가 없는 환경이다")
        self.assertEqual(discovery.local_ips()[0], primary)


class TestBroadcastTargets(unittest.TestCase):
    def test_limited_broadcast_is_always_included(self):
        self.assertIn("255.255.255.255", discovery.broadcast_targets("192.168.0.7"))

    def test_subnet_broadcast_is_derived(self):
        self.assertIn("192.168.219.255", discovery.broadcast_targets("192.168.219.109"))

    def test_without_an_interface_only_the_limited_broadcast(self):
        self.assertEqual(discovery.broadcast_targets(None), ["255.255.255.255"])

    def test_a_broadcast_address_is_not_doubled(self):
        self.assertEqual(discovery.broadcast_targets("10.0.0.255"), ["255.255.255.255"])


class TestAnnounceCoversEveryInterface(unittest.TestCase):
    """Windows 에서 가상 어댑터가 브로드캐스트를 삼키던 문제의 회귀 테스트."""

    def make(self):
        return discovery.Discovery("node-1", "pc", 9600, "testos", udp_port=19602)

    def record(self, disco, ips):
        """`_announce_from` 을 기록기로 갈아끼우고 어디로 보내려 했는지 본다."""
        seen = []
        disco._announce_from = lambda payload, ip: seen.append(ip) or True
        original = discovery.local_ips
        discovery.local_ips = lambda: list(ips)
        self.addCleanup(lambda: setattr(discovery, "local_ips", original))
        disco._announce_once(b"{}")
        return seen

    def test_every_interface_gets_its_own_send(self):
        disco = self.make()
        seen = self.record(disco, ["192.168.0.10", "172.20.5.1", "10.0.75.1"])
        self.assertEqual(seen, ["192.168.0.10", "172.20.5.1", "10.0.75.1"])

    def test_falls_back_to_an_unbound_send_when_no_address_is_known(self):
        disco = self.make()
        self.assertEqual(self.record(disco, []), [None])

    def test_falls_back_when_every_interface_fails(self):
        """인터페이스별 전송이 모두 실패하면 지정 없이 한 번은 보낸다."""
        disco = self.make()
        seen = []

        def failing(payload, ip):
            seen.append(ip)
            return ip is None  # 묶어서 보내는 것만 실패한다

        disco._announce_from = failing
        original = discovery.local_ips
        discovery.local_ips = lambda: ["192.168.0.10"]
        self.addCleanup(lambda: setattr(discovery, "local_ips", original))

        disco._announce_once(b"{}")
        self.assertEqual(seen, ["192.168.0.10", None])


class TestAnnounceReachesAListener(unittest.TestCase):
    """실제 UDP 로 한 번은 확인한다. 소켓 옵션을 빠뜨리면 여기서 걸린다."""

    PORT = 19603

    def test_a_listener_on_this_machine_receives_the_announcement(self):
        got = []

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", self.PORT))
        sock.settimeout(3.0)
        self.addCleanup(sock.close)

        def listen():
            try:
                got.append(sock.recvfrom(4096)[0])
            except (socket.timeout, OSError):
                pass

        thread = threading.Thread(target=listen)
        thread.start()
        time.sleep(0.2)

        disco = discovery.Discovery("node-x", "보내는쪽", 9600, "testos", udp_port=self.PORT)
        disco._announce_once(b'{"v": %d, "id": "node-x", "port": 9600}' % config.PROTOCOL_VERSION)
        thread.join(4)

        self.assertTrue(got, "브로드캐스트가 같은 PC 의 리스너에도 닿지 않았다")


class TestPacketHandling(unittest.TestCase):
    def make(self):
        return discovery.Discovery("me", "나", 9600, "testos", udp_port=19604)

    def packet(self, **over):
        import json

        msg = {"v": config.PROTOCOL_VERSION, "id": "other", "name": "상대", "port": 9600}
        msg.update(over)
        return json.dumps(msg).encode("utf-8")

    def test_my_own_announcement_is_ignored(self):
        disco = self.make()
        disco._on_packet(self.packet(id="me"), "192.168.0.9")
        self.assertEqual(disco.peers(), [])

    def test_host_is_taken_from_the_packet_source(self):
        """알림 안의 주소를 믿지 않고 실제로 온 주소를 쓴다."""
        disco = self.make()
        disco._on_packet(self.packet(), "192.168.0.9")
        self.assertEqual(disco.peers()[0].host, "192.168.0.9")

    def test_a_moved_peer_updates_its_address(self):
        """유선에서 무선으로 옮기면 같은 노드가 다른 주소로 온다."""
        disco = self.make()
        disco._on_packet(self.packet(), "192.168.0.9")
        disco._on_packet(self.packet(), "192.168.0.30")

        peers = disco.peers()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0].host, "192.168.0.30")

    def test_other_versions_are_ignored(self):
        disco = self.make()
        disco._on_packet(self.packet(v=config.PROTOCOL_VERSION + 1), "192.168.0.9")
        self.assertEqual(disco.peers(), [])

    def test_garbage_does_not_raise(self):
        disco = self.make()
        for junk in (b"", b"{", b"\xff\xfe", b'{"v": 1}', b'[1, 2]'):
            disco._on_packet(junk, "192.168.0.9")
        self.assertEqual(disco.peers(), [])


if __name__ == "__main__":
    unittest.main()
