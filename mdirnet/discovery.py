# UDP 브로드캐스트로 같은 공유기 안의 다른 MDirNet 노드를 찾아내는 모듈

import json
import socket
import threading
import time

from . import config


def local_ip():
    """공유기 쪽으로 나가는 인터페이스의 IP 를 알아낸다.

    실제로 패킷을 보내지는 않는다. UDP 소켓을 connect 하면 커널이 라우팅 테이블을
    보고 출발지 주소를 정해주는데, 그 값을 읽는 관용적인 방법이다.
    socket.gethostbyname(gethostname()) 은 127.0.0.1 을 주는 경우가 많아 쓰지 않는다.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 53))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class Peer:
    """발견된 상대 노드 하나."""

    def __init__(self, node_id, name, host, port, platform):
        self.node_id = node_id
        self.name = name
        self.host = host
        self.port = port
        self.platform = platform
        self.last_seen = time.monotonic()

    @property
    def label(self):
        return "%s  (%s:%d)" % (self.name, self.host, self.port)


class Discovery:
    """자기 존재를 알리는 announcer 와 남의 소식을 듣는 listener 를 함께 돌린다."""

    def __init__(self, node_id, name, tcp_port, platform, udp_port=config.UDP_PORT):
        self.node_id = node_id
        self.name = name
        self.tcp_port = tcp_port
        self.platform = platform
        self.udp_port = udp_port

        self._peers = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads = []

    def start(self):
        self._threads = [
            threading.Thread(target=self._announce_loop, name="announcer", daemon=True),
            threading.Thread(target=self._listen_loop, name="discovery", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        self._stop.set()

    def peers(self):
        """살아있는 피어 목록. TTL 이 지난 항목은 이 시점에 걸러낸다."""
        now = time.monotonic()
        with self._lock:
            for node_id, peer in list(self._peers.items()):
                if now - peer.last_seen > config.PEER_TTL:
                    del self._peers[node_id]
            return sorted(self._peers.values(), key=lambda p: p.name.lower())

    # --- announcer ---

    def _announce_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = json.dumps(
            {
                "v": config.PROTOCOL_VERSION,
                "id": self.node_id,
                "name": self.name,
                "port": self.tcp_port,
                "platform": self.platform,
            }
        ).encode("utf-8")

        try:
            while not self._stop.is_set():
                try:
                    sock.sendto(payload, ("255.255.255.255", self.udp_port))
                except OSError:
                    pass  # 인터페이스가 잠깐 내려간 경우. 다음 주기에 다시 시도한다
                self._stop.wait(config.ANNOUNCE_INTERVAL)
        finally:
            sock.close()

    # --- listener ---

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 같은 PC 에서 두 인스턴스를 띄워 시험하려면 둘 다 이 포트를 받아야 한다
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass

        try:
            sock.bind(("", self.udp_port))
        except OSError:
            sock.close()
            return  # 포트를 못 잡으면 발견 기능만 조용히 포기한다. 직접 연결은 계속 된다

        sock.settimeout(0.5)
        try:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._on_packet(data, addr[0])
        finally:
            sock.close()

    def _on_packet(self, data, host):
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(msg, dict) or msg.get("v") != config.PROTOCOL_VERSION:
            return

        node_id = msg.get("id")
        port = msg.get("port")
        if not isinstance(node_id, str) or not isinstance(port, int):
            return
        if node_id == self.node_id:
            return  # 내 브로드캐스트가 나에게 돌아온 것

        with self._lock:
            existing = self._peers.get(node_id)
            if existing:
                existing.last_seen = time.monotonic()
                existing.host, existing.port = host, port
                existing.name = str(msg.get("name") or existing.name)
            else:
                self._peers[node_id] = Peer(
                    node_id,
                    str(msg.get("name") or host),
                    host,
                    port,
                    str(msg.get("platform") or "?"),
                )
