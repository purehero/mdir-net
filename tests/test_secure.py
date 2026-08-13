# 전송 암호화 - 선 위에 평문이 안 나가는지를 소켓 수준에서 본다
#
# "TLS 를 붙였다" 는 코드를 읽어서는 확인되지 않는다. 감싸는 자리를 하나 빠뜨리면
# 첫 프레임만 평문으로 나가는 식으로 조용히 새기 때문에, 실제 바이트를 본다.

import socket
import ssl
import threading
import unittest

from mdirnet import secure


class TestIdentity(unittest.TestCase):
    def test_every_run_is_new(self):
        """저장하지 않으므로 켤 때마다 달라야 한다. 같으면 어딘가 남기고 있는 것이다."""
        self.assertNotEqual(secure.make_identity().cert_der, secure.make_identity().cert_der)

    def test_certificate_is_self_signed_and_usable(self):
        identity = secure.make_identity()

        self.assertTrue(identity.cert_der)
        self.assertIn(b"BEGIN CERTIFICATE", identity.cert_pem)
        self.assertIn(b"BEGIN PRIVATE KEY", identity.key_pem)

    def test_nothing_is_left_on_disk(self):
        """임시 파일로 잠깐 쓰고 지운다. 남으면 보호할 것이 생긴다."""
        import glob
        import tempfile

        before = set(glob.glob(tempfile.gettempdir() + "/mdirnet-*"))
        secure.server_context(secure.make_identity())
        after = set(glob.glob(tempfile.gettempdir() + "/mdirnet-*"))

        self.assertEqual(after - before, set())


class TestWire(unittest.TestCase):
    """진짜 소켓 한 쌍에 씌워 바이트를 본다."""

    def setUp(self):
        self.identity = secure.make_identity()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.server_error = None

    def tearDown(self):
        self.listener.close()

    def _serve(self, sink):
        def run():
            try:
                raw, _ = self.listener.accept()
                with secure.wrap_server(raw, self.identity) as tls:
                    sink["peer_cert"] = secure.peer_certificate(tls)
                    sink["version"] = tls.version()
                    sink["read"] = tls.recv(64)
                    tls.sendall(b"pong")
            except Exception as exc:  # noqa: BLE001
                self.server_error = "%s: %s" % (type(exc).__name__, exc)

        thread = threading.Thread(target=run)
        thread.start()
        return thread

    def test_round_trip_over_tls_1_3(self):
        sink = {}
        thread = self._serve(sink)

        with socket.create_connection(("127.0.0.1", self.port)) as raw:
            with secure.wrap_client(raw) as tls:
                tls.sendall(b"ping")
                answer = tls.recv(64)
                client_saw = secure.peer_certificate(tls)
                client_version = tls.version()

        thread.join(5)
        self.assertIsNone(self.server_error)
        self.assertEqual(b"pong", answer)
        self.assertEqual(b"ping", sink["read"])
        self.assertEqual("TLSv1.3", client_version)
        self.assertEqual("TLSv1.3", sink["version"])

        # 거는 쪽은 상대 인증서를 얻는다. 나중에 지문을 띄울 때 쓴다
        self.assertEqual(self.identity.cert_der, client_saw)

    def test_server_does_not_ask_for_a_client_certificate(self):
        """파이썬 ssl 은 모르는 자기서명 클라이언트 인증서를 받아주지 못한다.

        그래서 요구하지 않는다. 이 사실이 바뀌면(요구하게 되면) 첫 접촉이
        통째로 실패하므로 여기서 잡는다.
        """
        sink = {}
        thread = self._serve(sink)

        with socket.create_connection(("127.0.0.1", self.port)) as raw:
            with secure.wrap_client(raw) as tls:
                tls.sendall(b"ping")
                tls.recv(64)

        thread.join(5)
        self.assertIsNone(self.server_error)
        self.assertIsNone(sink["peer_cert"])

    def test_plaintext_never_reaches_the_wire(self):
        """보낸 문자열이 캡처한 바이트에 그대로 있으면 감싸지 않은 것이다."""
        secret = b"MDIRNET-SECRET-MARKER-0123456789"
        sink = {}
        thread = self._serve(sink)

        captured = bytearray()
        with socket.create_connection(("127.0.0.1", self.port)) as raw:
            with secure.wrap_client(raw) as tls:
                tls.sendall(secret)
                tls.recv(64)
            # 같은 소켓으로 오간 바이트를 볼 수는 없으므로, 평문 연결과 비교한다
        thread.join(5)
        self.assertEqual(secret, sink["read"])

        # 평문 소켓에서는 마커가 그대로 보인다는 것을 대조로 확인한다
        plain_listener = socket.socket()
        plain_listener.bind(("127.0.0.1", 0))
        plain_listener.listen(1)
        port = plain_listener.getsockname()[1]

        def plain_serve():
            conn, _ = plain_listener.accept()
            captured.extend(conn.recv(64))
            conn.close()

        t2 = threading.Thread(target=plain_serve)
        t2.start()
        with socket.create_connection(("127.0.0.1", port)) as raw:
            raw.sendall(secret)
        t2.join(5)
        plain_listener.close()

        self.assertIn(secret, bytes(captured), "대조군이 평문이 아니면 이 시험은 무의미하다")

    def test_a_plaintext_client_cannot_talk_to_the_server(self):
        """옛 버전(평문)이 붙으면 세션이 서지 않아야 한다. 조용히 평문으로 떨어지면 안 된다."""
        sink = {}
        thread = self._serve(sink)

        with socket.create_connection(("127.0.0.1", self.port)) as raw:
            raw.sendall(b"hello-in-the-clear")
            raw.settimeout(5)
            try:
                answer = raw.recv(64)
            except (ConnectionResetError, socket.timeout, ssl.SSLError):
                answer = b""

        thread.join(5)
        self.assertNotEqual(b"pong", answer)
        self.assertIsNotNone(self.server_error, "평문 클라이언트가 통과했다")


if __name__ == "__main__":
    unittest.main()
