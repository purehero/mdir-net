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


def local_ips():
    """브로드캐스트를 내보낼 후보 인터페이스 주소들.

    한 번의 `sendto("255.255.255.255")` 는 **인터페이스 하나로만** 나간다.
    어느 하나인지는 라우팅 테이블이 정하는데, Windows 에서는 Hyper-V, WSL,
    VirtualBox, VPN 가상 어댑터가 그 자리를 차지하는 일이 흔하다. 그러면
    브로드캐스트가 LAN 으로 나가지 않아 상대가 나를 찾지 못한다. 인터페이스마다
    소켓을 묶어 따로 보내려면 주소 목록이 필요하다.

    표준 라이브러리만으로는 인터페이스를 열거할 방법이 없어 두 가지를 합친다.
    라우팅 테이블이 고른 주소(`local_ip`)와 호스트 이름에 딸린 주소들이다.
    뒤쪽은 Windows 에서 가상 어댑터까지 다 돌려주고, macOS 에서는 `.local`
    이름이 풀리지 않아 실패하는 일이 많다. 그래서 둘 다 시도한다.
    """
    found = []

    def add(ip):
        if isinstance(ip, str) and ip and not ip.startswith("127.") and ip not in found:
            found.append(ip)

    add(local_ip())
    try:
        add(socket.gethostbyname(socket.gethostname()))
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass  # 호스트 이름이 풀리지 않는다. 라우팅 쪽 주소만 쓴다

    return found


def broadcast_targets(ip):
    """이 인터페이스로 보낼 목적지들.

    제한 브로드캐스트(255.255.255.255)와 서브넷 브로드캐스트를 함께 쓴다.
    넷마스크를 표준 라이브러리로 알 수 없어 가정 공유기에서 사실상 표준인
    /24 로 본다. 가정이 틀려도 제한 브로드캐스트가 그 인터페이스 전체를
    덮으므로 손해는 없다. 상대는 같은 알림을 두 번 받지만 node_id 로
    갱신하므로 결과는 같다.
    """
    targets = ["255.255.255.255"]
    if ip:
        head, _, tail = ip.rpartition(".")
        if head and tail != "255":
            targets.append(head + ".255")
    return targets


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
        payload = json.dumps(
            {
                "v": config.PROTOCOL_VERSION,
                "id": self.node_id,
                "name": self.name,
                "port": self.tcp_port,
                "platform": self.platform,
            }
        ).encode("utf-8")

        while not self._stop.is_set():
            self._announce_once(payload)
            self._stop.wait(config.ANNOUNCE_INTERVAL)

    def _announce_once(self, payload):
        """모든 인터페이스로 한 바퀴 알린다.

        주기마다 주소를 다시 알아본다. Wi-Fi 를 다시 붙거나 VPN 을 켜면 주소가
        바뀌는데, 시작할 때 한 번만 잡아두면 그때부터 아무에게도 안 보인다.
        """
        sent = False
        for ip in local_ips():
            sent = self._announce_from(payload, ip) or sent

        if not sent:
            # 주소를 못 알아냈거나 전부 실패했다. 인터페이스를 지정하지 않고
            # 한 번은 보낸다. 인터페이스가 하나인 PC 는 이것으로 충분하다
            self._announce_from(payload, None)

    def _announce_from(self, payload, ip):
        """이 인터페이스에 소켓을 묶어 보낸다. 하나라도 나갔으면 True."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            if ip:
                sock.bind((ip, 0))
        except OSError:
            sock.close()
            return False  # 방금 내려간 인터페이스. 다음 주기에 다시 본다

        ok = False
        try:
            for dest in broadcast_targets(ip):
                try:
                    sock.sendto(payload, (dest, self.udp_port))
                    ok = True
                except OSError:
                    pass  # 이 목적지만 실패. 다른 목적지를 계속 시도한다
        finally:
            sock.close()
        return ok

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
