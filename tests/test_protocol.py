# 프레이밍 계층의 왕복과 상한 검사를 확인하는 테스트

import socket
import unittest

from mdirnet import config, protocol


class FakeSock:
    """recv 를 잘게 쪼개 돌려주며 부분 수신 상황을 재현한다."""

    def __init__(self, data, chunk=1):
        self._data = bytes(data)
        self._pos = 0
        self._chunk = chunk

    def recv(self, want):
        end = min(self._pos + min(want, self._chunk), len(self._data))
        out = self._data[self._pos : end]
        self._pos = end
        return out


class TestFraming(unittest.TestCase):
    def test_header_only_roundtrip(self):
        frame = protocol.encode({"t": "list", "id": 3, "path": "/tmp"})
        header, blob = protocol.FrameReader(FakeSock(frame, chunk=4096)).read()
        self.assertEqual(header, {"t": "list", "id": 3, "path": "/tmp"})
        self.assertIsNone(blob)

    def test_blob_roundtrip(self):
        payload = bytes(range(256)) * 40
        frame = protocol.encode({"t": "data", "id": 1}, payload)
        header, blob = protocol.FrameReader(FakeSock(frame, chunk=4096)).read()
        self.assertEqual(header, {"t": "data", "id": 1})
        self.assertEqual(blob, payload)

    def test_blob_length_is_not_leaked_into_header(self):
        """'blob' 은 프레이밍용 필드다. 읽는 쪽 헤더에 남아 있으면 안 된다."""
        header, _ = protocol.FrameReader(FakeSock(protocol.encode({"t": "x"}, b"ab"), 4096)).read()
        self.assertNotIn("blob", header)

    def test_split_recv_reassembles(self):
        payload = b"\x00\xffabc" * 500
        frame = protocol.encode({"t": "data", "id": 9}, payload)
        # 1바이트씩 흘려줘도 정확히 재조립되어야 한다
        header, blob = protocol.FrameReader(FakeSock(frame, chunk=1)).read()
        self.assertEqual(header["id"], 9)
        self.assertEqual(blob, payload)

    def test_consecutive_frames(self):
        stream = protocol.encode({"t": "a", "id": 1}) + protocol.encode({"t": "b", "id": 2}, b"zz")
        reader = protocol.FrameReader(FakeSock(stream, chunk=3))
        first, _ = reader.read()
        second, blob = reader.read()
        self.assertEqual(first["t"], "a")
        self.assertEqual(second["t"], "b")
        self.assertEqual(blob, b"zz")

    def test_peer_closed(self):
        with self.assertRaises(protocol.PeerClosed):
            protocol.FrameReader(FakeSock(b"")).read()

    def test_truncated_frame_is_peer_closed(self):
        frame = protocol.encode({"t": "data", "id": 1}, b"0123456789")
        with self.assertRaises(protocol.PeerClosed):
            protocol.FrameReader(FakeSock(frame[:-4], chunk=4096)).read()

    def test_oversized_blob_rejected_on_encode(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode({"t": "data"}, b"x" * (config.MAX_BLOB_BYTES + 1))

    def test_oversized_header_length_rejected_on_read(self):
        bogus = (config.MAX_HEADER_BYTES + 1).to_bytes(4, "big")
        with self.assertRaises(protocol.ProtocolError):
            protocol.FrameReader(FakeSock(bogus, chunk=4096)).read()

    def test_garbage_header_rejected(self):
        body = b"not json at all"
        frame = len(body).to_bytes(4, "big") + body
        with self.assertRaises(protocol.ProtocolError):
            protocol.FrameReader(FakeSock(frame, chunk=4096)).read()

    def test_non_object_header_rejected(self):
        body = b"[1,2,3]"
        frame = len(body).to_bytes(4, "big") + body
        with self.assertRaises(protocol.ProtocolError):
            protocol.FrameReader(FakeSock(frame, chunk=4096)).read()

    def test_lying_blob_length_rejected(self):
        body = b'{"t":"data","blob":%d}' % (config.MAX_BLOB_BYTES + 1)
        frame = len(body).to_bytes(4, "big") + body
        with self.assertRaises(protocol.ProtocolError):
            protocol.FrameReader(FakeSock(frame, chunk=4096)).read()

    def test_unicode_header_survives(self):
        frame = protocol.encode({"t": "list", "path": "/사용자/문서", "msg": "권한 없음"})
        header, _ = protocol.FrameReader(FakeSock(frame, chunk=4096)).read()
        self.assertEqual(header["path"], "/사용자/문서")
        self.assertEqual(header["msg"], "권한 없음")

    def test_send_frame_over_real_socket(self):
        a, b = socket.socketpair()
        try:
            protocol.send_frame(a, {"t": "hello", "name": "inka-mac"}, b"payload")
            header, blob = protocol.FrameReader(b).read()
            self.assertEqual(header["name"], "inka-mac")
            self.assertEqual(blob, b"payload")
        finally:
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
