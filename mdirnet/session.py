# TCP 세션 수립(요청/승인)과 양방향 요청-응답, 파일 전송을 담당하는 모듈

import itertools
import os
import queue
import socket
import ssl
import threading
from pathlib import Path

from . import config, fingerprint as fingerprint_mod, fsops, protocol, secure, shares as shares_mod


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

    def __init__(self, sock, peer_name, peer_host, shares, events):
        #: 상대 인증서의 지문. **거는 쪽만** 갖는다 - 서버만 인증서를 내밀기 때문이다.
        #: 받는 쪽은 자기 지문(Server.fingerprint)을 띄운다 (PROTOCOL.md 2.5절)
        self.peer_fingerprint = None
        self._sock = sock
        self.peer_name = peer_name
        self.peer_host = peer_host
        # 공유 폴더 목록. 원격 경로를 실제 경로로 바꾸는 유일한 관문이다
        self._shares = shares
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
        except SessionError:
            pass  # 이미 끊긴 뒤라면 인사를 못 보내도 상관없다
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
            try:
                protocol.send_frame(self._sock, header, blob)
            except OSError as exc:
                # 전송 중 연결이 끊기면 닫힌 소켓에 쓰다 OSError 가 난다. 이걸
                # 그대로 올려보내면 워커 스레드에서 트레이스백이 터져 나온다.
                # 호출하는 쪽이 한 종류만 잡으면 되도록 여기서 바꿔준다
                raise SessionError("연결이 끊어졌습니다: %s" % exc) from exc

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
        """(파일 목록, 하위 디렉터리 목록). 빈 폴더도 복사해야 하므로 둘 다 받는다."""
        reply = self.request("tree", path=path)
        return reply["files"], reply.get("dirs", [])

    def remote_head(self, path, limit):
        """뷰어용. 파일 앞부분만 받아온다."""
        req_id, box = self._open_box()
        try:
            self._send_q({"t": "head", "id": req_id, "path": path, "limit": limit})
            header, blob = self._await(box)
            return blob or b"", header.get("size", 0)
        finally:
            self._close_box(self._outgoing, req_id)

    def remote_mkdir(self, path):
        self.request("mkdir", path=path)

    def remote_delete(self, path):
        self.request("delete", path=path)

    def remote_rename(self, path, new_name):
        self.request("rename", path=path, name=new_name)

    # --- 전송: 상대 > 나 ---

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

    # --- 전송: 나 > 상대 ---

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
        except protocol.ProtocolError as exc:
            self.close("프로토콜 오류: %s" % exc)
        except Exception as exc:  # noqa: BLE001
            # 어떤 예외가 나더라도 **반드시 답을 보낸다.** 답이 없으면 상대는
            # 요청 타임아웃(30초)까지 그대로 멈춰 있게 된다. 예상 못한 버그가
            # 상대를 얼어붙게 만드는 것이 조용히 실패하는 것보다 훨씬 나쁘다
            self._reply_err(req_id, "%s: %s" % (type(exc).__name__, exc))
        finally:
            self._close_box(self._incoming, req_id)

    def _reply_err(self, req_id, msg):
        try:
            self._send({"t": "err", "id": req_id, "msg": msg})
        except SessionError:
            pass  # 오류를 알려주려는 순간 연결이 끊겼다면 더 할 수 있는 게 없다

    def _safe(self, raw_path):
        """원격이 보낸 상대 경로를 실제 경로로 바꾼다. 목록 밖은 거부된다."""
        return self._shares.resolve(raw_path)

    def _on_home(self, req_id, header, blob, box):
        # 상대는 늘 가상 루트에서 시작한다. 내 호스트 경로는 알려주지 않는다
        self._send({"t": "home.ok", "id": req_id, "path": shares_mod.ROOT})

    def _on_list(self, req_id, header, blob, box):
        remote_path = header.get("path") or shares_mod.ROOT

        if self._shares.is_root(remote_path):
            entries = self._shares.root_entries()
        else:
            entries = fsops.list_dir(self._safe(remote_path))

        # 돌려주는 path 도 원격 경로 공간이어야 한다. 상대가 그대로 화면에
        # 쓰고 다음 요청에 다시 보내기 때문이다
        self._send(
            {
                "t": "list.ok",
                "id": req_id,
                "path": shares_mod.normalize(remote_path),
                "entries": entries,
            }
        )

    def _on_stat(self, req_id, header, blob, box):
        if self._shares.is_root(header["path"]):
            self._send(
                {"t": "stat.ok", "id": req_id, "exists": True, "is_dir": True, "size": 0}
            )
            return
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
        self._send(
            {
                "t": "tree.ok",
                "id": req_id,
                "files": fsops.walk_tree(path),
                "dirs": fsops.walk_dirs(path),
            }
        )

    def _on_head(self, req_id, header, blob, box):
        path = self._safe(header["path"])
        if not path.is_file():
            raise ValueError("파일이 아닙니다: %s" % path.name)

        # 음수를 그대로 넘기면 read(-1) 이 되어 파일을 통째로 읽는다. 상한이
        # 우회되므로 0 아래로는 내려가지 않게 잡는다
        limit = max(min(int(header.get("limit") or 0), config.MAX_VIEW_BYTES), 0)
        self._send(
            {"t": "head.ok", "id": req_id, "size": path.stat().st_size},
            fsops.read_head(path, limit),
        )

    def _on_mkdir(self, req_id, header, blob, box):
        if self._shares.is_root(header["path"]):
            raise ValueError("공유 목록에는 폴더를 만들 수 없습니다")
        path = self._safe(header["path"])
        path.mkdir(parents=False, exist_ok=False)
        self._send({"t": "mkdir.ok", "id": req_id})

    def _on_delete(self, req_id, header, blob, box):
        """원격 삭제. 공유 폴더 자체와 가상 루트는 절대 지울 수 없다."""
        if self._shares.is_root(header["path"]):
            raise ValueError("공유 목록은 지울 수 없습니다")

        path = self._safe(header["path"])
        share = self._shares.share_of(header["path"])
        if share is not None and path == share.path:
            raise ValueError("공유 폴더 자체는 지울 수 없습니다. 목록에서 빼세요")
        if not path.exists() and not path.is_symlink():
            raise ValueError("없는 항목입니다: %s" % path.name)

        fsops.remove_tree(path)
        self._send({"t": "delete.ok", "id": req_id})
        self._events.put(("remote_changed", str(path.parent)))

    def _on_rename(self, req_id, header, blob, box):
        """원격 이름 바꾸기. 덮어쓰지 않고, 공유 폴더 자체는 못 바꾸게 막는다."""
        if self._shares.is_root(header["path"]):
            raise ValueError("공유 목록은 이름을 바꿀 수 없습니다")

        name = str(header.get("name") or "")
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise ValueError("이름이 올바르지 않습니다")

        path = self._safe(header["path"])
        share = self._shares.share_of(header["path"])
        if share is not None and path == share.path:
            raise ValueError("공유 폴더 자체는 이름을 바꿀 수 없습니다")

        # 새 이름도 같은 공유 폴더 안쪽인지 검사한다
        target = fsops.resolve_within(share.path, path.parent / name)
        if target.exists() or target.is_symlink():
            raise ValueError("같은 이름이 이미 있습니다: %s" % name)

        os.rename(path, target)
        self._send({"t": "rename.ok", "id": req_id})
        self._events.put(("remote_changed", str(path.parent)))

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
    "head": Session._on_head,
    "mkdir": Session._on_mkdir,
    "delete": Session._on_delete,
    "rename": Session._on_rename,
    "pull": Session._on_pull,
    "push.begin": Session._on_push_begin,
}


# --- 연결 수립 ---


def connect(peer, my_name, platform, shares, events):
    """상대에게 연결을 요청하고 승인을 기다린다. 승인되면 Session 을 돌려준다."""
    raw = socket.create_connection((peer.host, peer.port), timeout=config.CONNECT_TIMEOUT)
    try:
        # 프레임을 하나라도 보내기 **전에** 감싼다. hello 도 평문으로 나가면 안 된다
        sock = secure.wrap_client(raw)
    except (ssl.SSLError, OSError) as exc:
        secure.close_quietly(raw)
        raise SessionError("암호화 연결을 세우지 못했습니다: %s" % exc)

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
        # 상대 인증서의 지문. 받는 쪽 화면의 `이 기기 지문` 과 같아야 한다
        der = secure.peer_certificate(sock)
        peer_fp = fingerprint_mod.fingerprint(der) if der else None

        session = Session(sock, str(header.get("name") or peer.name), peer.host, shares, events)
        session.peer_fingerprint = peer_fp
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

    def __init__(self, my_name, platform, shares, events, port=config.TCP_PORT, identity=None):
        # 프로세스 한 판짜리 신원. 주지 않으면 여기서 만든다
        self.identity = identity or secure.make_identity()
        # 이 기기의 지문. 화면에 띄워 상대와 대조한다 (PROTOCOL.md 2.5절)
        self.fingerprint = fingerprint_mod.fingerprint(self.identity.cert_der)
        self.my_name = my_name
        self.platform = platform
        # 목록 객체를 그대로 들고 있으므로, 앱에서 항목을 더하면 곧바로 반영된다
        self.shares = shares
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

    def _greet(self, raw, host):
        """hello 를 읽고 UI 에 승인을 물어본 뒤 결과를 회신한다."""
        try:
            raw.settimeout(config.CONNECT_TIMEOUT)
            # 첫 프레임을 읽기 **전에** 감싼다
            sock = secure.wrap_server(raw, self.identity)
        except (ssl.SSLError, OSError):
            # 옛 버전이나 엉뚱한 것이 붙은 경우다. 조용히 끊는다
            secure.close_quietly(raw)
            return

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
                # UI 에 알리지 않으면, 늦게 수락을 눌렀을 때 창만 닫히고
                # 아무 일도 일어나지 않아 사용자는 수락됐다고 믿게 된다
                self.events.put(("approval_expired", approval))
                protocol.send_frame(sock, {"t": "hello.no"})
                sock.close()
                return

            protocol.send_frame(
                sock, {"t": "hello.ok", "name": self.my_name, "platform": self.platform}
            )
            sock.settimeout(None)

            session = Session(sock, peer_name, host, self.shares, self.events)
            session.start()
            self.events.put(("connected", session))
        except (OSError, protocol.ProtocolError, protocol.PeerClosed):
            try:
                sock.close()
            except OSError:
                pass
