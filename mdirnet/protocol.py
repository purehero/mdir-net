# TCP 위에서 JSON 헤더와 raw 바이너리를 함께 실어보내는 프레이밍 계층

import json
import struct

from . import config

_LEN = struct.Struct(">I")


class ProtocolError(Exception):
    """프레임이 규격에 맞지 않을 때. 발생하면 연결을 끊는다."""


class PeerClosed(Exception):
    """상대가 연결을 정상/비정상 종료했을 때."""


def encode(header, blob=None):
    """헤더 dict 와 선택적 raw 바이트를 하나의 프레임으로 직렬화한다.

    프레임 = 4바이트 헤더 길이 + UTF-8 JSON 헤더 + (blob 바이트)
    blob 이 있으면 헤더에 그 길이를 'blob' 키로 박아둔다. 리더가 헤더를 읽은
    직후 정확히 그만큼 더 읽으면 되므로 별도 구분자가 필요없다.
    """
    if blob is not None:
        if len(blob) > config.MAX_BLOB_BYTES:
            raise ProtocolError("blob 이 상한을 넘었습니다: %d" % len(blob))
        header = dict(header, blob=len(blob))

    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > config.MAX_HEADER_BYTES:
        raise ProtocolError("헤더가 상한을 넘었습니다: %d" % len(raw))

    out = _LEN.pack(len(raw)) + raw
    return out + blob if blob else out


class FrameReader:
    """소켓에서 프레임을 하나씩 꺼내 읽는다.

    한 연결에 리더는 반드시 하나여야 한다. 헤더와 blob 을 연속으로 읽는 구조라
    둘 이상이 같은 소켓을 읽으면 스트림이 섞여 프로토콜이 깨진다.
    """

    def __init__(self, sock):
        self._sock = sock
        self._buf = bytearray()

    def read(self):
        """다음 프레임을 (header, blob) 으로 반환한다. blob 이 없으면 None."""
        raw = self._read_exactly(_LEN.size)
        (size,) = _LEN.unpack(raw)
        if size > config.MAX_HEADER_BYTES:
            raise ProtocolError("헤더 길이가 상한을 넘었습니다: %d" % size)

        try:
            header = json.loads(self._read_exactly(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("헤더를 해석할 수 없습니다: %s" % exc)
        if not isinstance(header, dict):
            raise ProtocolError("헤더가 객체가 아닙니다")

        blob_len = header.pop("blob", 0)
        if not isinstance(blob_len, int) or blob_len < 0:
            raise ProtocolError("blob 길이가 올바르지 않습니다")
        if blob_len > config.MAX_BLOB_BYTES:
            raise ProtocolError("blob 길이가 상한을 넘었습니다: %d" % blob_len)

        return header, (self._read_exactly(blob_len) if blob_len else None)

    def _read_exactly(self, n):
        """정확히 n 바이트를 모을 때까지 recv 를 반복한다."""
        while len(self._buf) < n:
            # 남은 양보다 넉넉히 요청해 recv 호출 횟수를 줄인다
            want = max(n - len(self._buf), 64 * 1024)
            chunk = self._sock.recv(want)
            if not chunk:
                raise PeerClosed("상대가 연결을 닫았습니다")
            self._buf.extend(chunk)

        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


def send_frame(sock, header, blob=None):
    """프레임 하나를 소켓에 완전히 써넣는다."""
    sock.sendall(encode(header, blob))
