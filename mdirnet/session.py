# TCP 세션 수립(요청/승인)과 양방향 요청-응답, 파일 전송을 담당하는 모듈

import itertools
import os
import queue
import socket
import threading
from pathlib import Path

from . import config, fsops, protocol


class SessionError(Exception):
    """세션 수준의 실패. 사용자에게 문자열로 보여줄 수 있는 오류."""


class Rejected(SessionError):
    """상대가 연결 요청을 거부했다."""


_CLOSED = object()  # 대기 중인 요청을 깨우는 센티널


class ApprovalRequest:
    """들어온 연결 요청 하나. UI 가 수락/거부를 정해주기를 기다린다."""

    def __init__(self, name, host, platform):
        self.name = name
        self.host = host
        self.platform = platform
        self._decided = threading.Event()
        self._accepted = False

    def decide(self, accepted):
        self._accepted = accepted
        self._decided.set()

    def wait(self, timeout):
        """UI 가 결정할 때까지 기다린다. 시간이 다 되면 거부로 처리한다."""
        if not self._decided.wait(timeout):
            return False
        return self._accepted


class Session:
    """수립된 연결 하나. 양쪽이 서로에게 요청할 수 있는 대칭 구조다.

    양쪽이 각자 1번부터 요청 id 를 매기므로 id 만으로는 어느 쪽 대화인지 알 수 없다.
    그래서 대화를 **시작한 쪽**이 보내는 프레임에는 'q' 표시를 붙인다.
    q 가 있으면 상대가 시작한 대화, 없으면 내가 시작한 대화의 응답이다.
    이 규칙이 없으면 내 pull 응답과 상대의 push 데이터가 같은 우편함으로 섞인다.
    """

    def __init__(self, sock, peer_name, peer_host, root, events):
        self._sock = sock
        self.peer_name = peer_name
        self.peer_host = peer_host
        self._root = Path(root)
        self._events = events

        self._reader = protocol.FrameReader(sock)
        self._send_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._outgoing = {}  # 내가 시작한 대화의 응답을 받는 우편함
        self._incoming = {}  # 상대가 시작한 대화의 후속 프레임을 받는 우편함
        self._box_lock = threading.Lock()
        self._closed = threading.Event()
        self._thread = None

    # --- 생명주기 ---

    def start(self):
        self._thread = threading.Thread(target=self._read_loop, name="session", daemon=True)
        self._thread.start()

    def close(self, reason=None):
        """소켓을 닫고 대기 중인 모든 요청을 깨운다. 두 번 불러도 안전하다."""
        if self._closed.is_set():
            return
        self._closed.set()

        try:
            self._send({"t": "bye"})
        except OSError:
            pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

        with self._box_lock:
            boxes = list(self._outgoing.values()) + list(self._incoming.values())
            self._outgoing.clear()
            self._incoming.clear()
        for box in boxes:
            box.put(_CLOSED)

        self._events.put(("disconnected", reason or "연결이 끊어졌습니다"))

    @property
    def closed(self):
        return self._closed.is_set()

    # --- 저수준 송수신 ---

    def _send(self, header, blob=None):
        # 여러 스레드가 같은 소켓에 쓴다. 프레임이 쪼개져 섞이지 않도록 직렬화한다
        with self._send_lock:
            protocol.send_frame(self._sock, header, blob)

    def _send_q(self, header, blob=None):
        """내가 시작한 대화의 프레임. 'q' 를 붙여 상대가 구분할 수 있게 한다."""
        self._send(dict(header, q=1), blob)

    def _read_loop(self):
        reason = None
        try:
            while not self._closed.is_set():
                header, blob = self._reader.read()
                kind = header.get("t")

                if kind == "bye":
                    reason = "%s 가 연결을 종료했습니다" % self.peer_name
                    break

                if header.get("q"):
                    self._route_peer_initiated(kind, header, blob)
                else:
                    self._route_to_box(self._outgoing, header, blob)
        except protocol.PeerClosed:
            reason = "%s 와의 연결이 끊어졌습니다" % self.peer_name
        except protocol.ProtocolError as exc:
            reason = "프로토콜 오류: %s" % exc
        except OSError as exc:
            reason = "네트워크 오류: %s" % exc
        finally:
            self.close(reason)

    def _route_peer_initiated(self, kind, header, blob):
        """상대가 시작한 대화. 첫 프레임이면 처리 스레드를 띄우고, 뒤이은 것은 넘긴다."""
        if kind not in _REQUESTS:
            self._route_to_box(self._incoming, header, blob)
            return

        # 우편함 등록은 **이 스레드에서** 해야 한다. 처리 스레드에 맡기면 곧바로
        # 뒤따라오는 data 프레임이 등록보다 먼저 도착해 버려질 수 있다
        req_id = header.get("id")
        box = queue.Queue()
        with self._box_lock:
            self._incoming[req_id] = box

        threading.Thread(
            target=self._handle_request,
            args=(header, blob, box),
            name="req-%s" % kind,
            daemon=True,
        ).start()

    def _route_to_box(self, table, header, blob):
        with self._box_lock:
            box = table.get(header.get("id"))
        if box is not None:
            box.put((header, blob))

    # --- 요청하는 쪽 ---

    def _open_box(self):
        req_id = next(self._ids)
        box = queue.Queue()
        with self._box_lock:
            self._outgoing[req_id] = box
        return req_id, box

    def _close_box(self, table, req_id):
        with self._box_lock:
            table.pop(req_id, None)

    def _await(self, box, timeout=config.REQUEST_TIMEOUT):
        """우편함에서 프레임 하나를 꺼낸다. 오류 응답은 예외로 바꿔 올린다."""
        try:
            item = box.get(timeout=timeout)
        except queue.Empty:
            raise SessionError("응답이 없습니다 (%.0f초 초과)" % timeout)
        if item is _CLOSED:
            raise SessionError("연결이 끊어졌습니다")

        header, blob = item
        if header.get("t") == "err":
            raise SessionError(str(header.get("msg") or "알 수 없는 오류"))
        return header, blob

    def request(self, kind, **fields):
        """단일 응답을 받는 평범한 요청."""
        req_id, box = self._open_box()
        try:
            self._send_q(dict(fields, t=kind, id=req_id))
            return self._await(box)[0]
        finally:
            self._close_box(self._outgoing, req_id)

    def remote_home(self):
        return self.request("home")["path"]

    def remote_list(self, path):
        reply = self.request("list", path=path)
        return reply["path"], reply["entries"]

    def remote_stat(self, path):
        return self.request("stat", path=path)

    def remote_tree(self, path):
        return self.request("tree", path=path)["files"]

    # --- 전송: 상대 → 나 ---

    def pull_file(self, remote_path, dest, on_progress=None):
        """상대 파일 하나를 dest 로 받는다. .part 로 받고 완료 후 rename 한다."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = fsops.unique_part_path(dest)

        req_id, box = self._open_box()
        received = 0
        try:
            self._send_q({"t": "pull", "id": req_id, "path": remote_path})
            with open(part, "wb") as fp:
                while True:
                    header, blob = self._await(box)
                    if header.get("t") == "data.end":
                        break
                    if blob:
                        fp.write(blob)
                        received += len(blob)
                        if on_progress:
                            on_progress(len(blob))
            os.replace(part, dest)
            return received
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        finally:
            self._close_box(self._outgoing, req_id)

    # --- 전송: 나 → 상대 ---

    def push_file(self, local_path, remote_path, on_progress=None):
        """내 파일 하나를 상대의 remote_path 로 보낸다."""
        local_path = Path(local_path)
        size = local_path.stat().st_size

        req_id, box = self._open_box()
        try:
            self._send_q({"t": "push.begin", "id": req_id, "path": remote_path, "size": size})
            self._await(box)  # push.ok. 경로가 거부되면 여기서 예외가 난다

            with open(local_path, "rb") as fp:
                while True:
                    chunk = fp.read(config.CHUNK_SIZE)
                    if not chunk:
                        break
                    self._send_q({"t": "data", "id": req_id}, chunk)
                    if on_progress:
                        on_progress(len(chunk))

            self._send_q({"t": "data.end", "id": req_id})
            self._await(box)  # push.done
            return size
        finally:
            self._close_box(self._outgoing, req_id)

    # --- 응답하는 쪽 ---

    def _handle_request(self, header, blob, box):
        req_id = header.get("id")
        try:
            _REQUESTS[header["t"]](self, req_id, header, blob, box)
        except fsops.PathDenied as exc:
            self._reply_err(req_id, str(exc))
        except (OSError, KeyError, ValueError, SessionError) as exc:
            self._reply_err(req_id, "%s" % exc)
        except protocol.ProtocolError as exc:
            self.close("프로토콜 오류: %s" % exc)
        finally:
            self._close_box(self._incoming, req_id)

    def _reply_err(self, req_id, msg):
        try:
            self._send({"t": "err", "id": req_id, "msg": msg})
        except OSError:
            pass

    def _safe(self, raw_path):
        """원격이 보낸 경로를 공유 루트 안쪽으로 제한한다."""
        return fsops.resolve_within(self._root, raw_path)

    def _on_home(self, req_id, header, blob, box):
        self._send({"t": "home.ok", "id": req_id, "path": str(self._root)})

    def _on_list(self, req_id, header, blob, box):
        path = self._safe(header.get("path") or str(self._root))
        self._send(
            {
                "t": "list.ok",
                "id": req_id,
                "path": str(path),
                "entries": fsops.list_dir(path),
            }
        )

    def _on_stat(self, req_id, header, blob, box):
        path = self._safe(header["path"])
        exists = path.exists()
        self._send(
            {
                "t": "stat.ok",
                "id": req_id,
                "exists": exists,
                "is_dir": exists and path.is_dir(),
                "size": path.stat().st_size if exists and path.is_file() else 0,
            }
        )

    def _on_tree(self, req_id, header, blob, box):
        path = self._safe(header["path"])
        self._send({"t": "tree.ok", "id": req_id, "files": fsops.walk_tree(path)})

    def _on_pull(self, req_id, header, blob, box):
        path = self._safe(header["path"])
        if not path.is_file():
            raise ValueError("파일이 아닙니다: %s" % path.name)

        with open(path, "rb") as fp:
            while True:
                chunk = fp.read(config.CHUNK_SIZE)
                if not chunk:
                    break
                self._send({"t": "data", "id": req_id}, chunk)
        self._send({"t": "data.end", "id": req_id})

    def _on_push_begin(self, req_id, header, blob, box):
        """밀어넣기를 수락하고, 같은 id 로 오는 data 프레임을 파일로 받아쓴다."""
        path = self._safe(header["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        part = fsops.unique_part_path(path)

        try:
            self._send({"t": "push.ok", "id": req_id})
            with open(part, "wb") as fp:
                while True:
                    item = box.get(timeout=config.REQUEST_TIMEOUT)
                    if item is _CLOSED:
                        raise OSError("전송 중 연결이 끊어졌습니다")
                    data_header, data_blob = item
                    if data_header.get("t") == "data.end":
                        break
                    if data_blob:
                        fp.write(data_blob)
            os.replace(part, path)
            self._send({"t": "push.done", "id": req_id})
            self._events.put(("received", str(path)))
        except queue.Empty:
            part.unlink(missing_ok=True)
            raise SessionError("전송이 중간에 멈췄습니다")
        except BaseException:
            part.unlink(missing_ok=True)
            raise


# 상대가 시작한 대화의 첫 프레임 타입. 여기 없으면 후속 프레임으로 취급한다
_REQUESTS = {
    "home": Session._on_home,
    "list": Session._on_list,
    "stat": Session._on_stat,
    "tree": Session._on_tree,
    "pull": Session._on_pull,
    "push.begin": Session._on_push_begin,
}


# --- 연결 수립 ---


def connect(peer, my_name, platform, root, events):
    """상대에게 연결을 요청하고 승인을 기다린다. 승인되면 Session 을 돌려준다."""
    sock = socket.create_connection((peer.host, peer.port), timeout=config.CONNECT_TIMEOUT)
    try:
        protocol.send_frame(
            sock,
            {
                "t": "hello",
                "v": config.PROTOCOL_VERSION,
                "name": my_name,
                "platform": platform,
            },
        )

        # 상대가 승인 버튼을 누를 시간을 준다. 여유를 조금 더 둔다
        sock.settimeout(config.APPROVAL_TIMEOUT + 5.0)
        header, _ = protocol.FrameReader(sock).read()

        if header.get("t") != "hello.ok":
            raise Rejected("%s 가 연결을 거부했습니다" % peer.name)

        sock.settimeout(None)
        session = Session(sock, str(header.get("name") or peer.name), peer.host, root, events)
        session.start()
        return session
    except socket.timeout:
        sock.close()
        raise SessionError("응답이 없습니다. 상대가 승인하지 않았습니다")
    except protocol.PeerClosed:
        sock.close()
        raise SessionError("상대가 연결을 닫았습니다")
    except BaseException:
        sock.close()
        raise


class Server:
    """들어오는 연결을 받아 UI 에 승인을 물어보는 TCP 서버."""

    def __init__(self, my_name, platform, root, events, port=config.TCP_PORT):
        self.my_name = my_name
        self.platform = platform
        self.root = root
        self.events = events
        self.port = port
        self._sock = None
        self._stop = threading.Event()

    def bind(self):
        """포트를 잡는다. 이미 쓰는 중이면 하나씩 올려가며 10번까지 시도한다.

        같은 PC 에서 두 인스턴스를 띄워 시험할 수 있어야 하므로 실패로 끝내지 않는다.
        실제로 잡은 포트를 self.port 에 반영하고, 그 값을 브로드캐스트한다.
        """
        base = self.port
        for offset in range(10):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("", base + offset))
            except OSError:
                sock.close()
                continue
            sock.listen(4)
            # port=0 으로 커널에 맡겼을 수도 있으니 실제로 잡힌 값을 읽어온다
            self._sock, self.port = sock, sock.getsockname()[1]
            return self.port

        raise SessionError("사용할 수 있는 포트를 찾지 못했습니다")

    def start(self):
        threading.Thread(target=self._accept_loop, name="accept", daemon=True).start()

    def stop(self):
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    def _accept_loop(self):
        self._sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                client, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._greet, args=(client, addr[0]), name="greet", daemon=True
            ).start()

    def _greet(self, sock, host):
        """hello 를 읽고 UI 에 승인을 물어본 뒤 결과를 회신한다."""
        try:
            sock.settimeout(config.CONNECT_TIMEOUT)
            header, _ = protocol.FrameReader(sock).read()

            if header.get("t") != "hello" or header.get("v") != config.PROTOCOL_VERSION:
                protocol.send_frame(sock, {"t": "hello.no", "msg": "버전이 맞지 않습니다"})
                sock.close()
                return

            peer_name = str(header.get("name") or host)
            approval = ApprovalRequest(peer_name, host, str(header.get("platform") or "?"))
            self.events.put(("approval", approval))

            if not approval.wait(config.APPROVAL_TIMEOUT):
                protocol.send_frame(sock, {"t": "hello.no"})
                sock.close()
                return

            protocol.send_frame(
                sock, {"t": "hello.ok", "name": self.my_name, "platform": self.platform}
            )
            sock.settimeout(None)

            session = Session(sock, peer_name, host, self.root, self.events)
            session.start()
            self.events.put(("connected", session))
        except (OSError, protocol.ProtocolError, protocol.PeerClosed):
            try:
                sock.close()
            except OSError:
                pass
