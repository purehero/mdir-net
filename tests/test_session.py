# 승인 핸드셰이크와 양방향 파일/디렉터리 전송을 루프백으로 확인하는 테스트

import hashlib
import queue
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from mdirnet import fsops, session


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SessionPairTestCase(unittest.TestCase):
    """socketpair 로 두 Session 을 맞물려 세운다. 승인 절차는 이미 끝난 상태로 본다."""

    def setUp(self):
        self.tmp_a = tempfile.TemporaryDirectory()
        self.tmp_b = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_a.cleanup)
        self.addCleanup(self.tmp_b.cleanup)
        self.root_a = Path(self.tmp_a.name).resolve()
        self.root_b = Path(self.tmp_b.name).resolve()

        sock_a, sock_b = socket.socketpair()
        self.events_a, self.events_b = queue.Queue(), queue.Queue()
        self.a = session.Session(sock_a, "node-b", "127.0.0.1", self.root_a, self.events_a)
        self.b = session.Session(sock_b, "node-a", "127.0.0.1", self.root_b, self.events_b)
        self.a.start()
        self.b.start()
        self.addCleanup(self.b.close)
        self.addCleanup(self.a.close)


class TestQueries(SessionPairTestCase):
    def test_remote_home(self):
        self.assertEqual(self.a.remote_home(), str(self.root_b))

    def test_remote_list(self):
        (self.root_b / "docs").mkdir()
        (self.root_b / "note.txt").write_bytes(b"hello")

        path, entries = self.a.remote_list(str(self.root_b))
        self.assertEqual(path, str(self.root_b))
        self.assertEqual([e["name"] for e in entries], ["docs", "note.txt"])
        self.assertEqual(entries[1]["size"], 5)

    def test_both_directions_work(self):
        """대칭 세션이므로 승인한 쪽도 상대를 조회할 수 있어야 한다."""
        (self.root_a / "from_a.txt").write_bytes(b"a")
        (self.root_b / "from_b.txt").write_bytes(b"b")

        self.assertIn("from_b.txt", [e["name"] for e in self.a.remote_list(str(self.root_b))[1]])
        self.assertIn("from_a.txt", [e["name"] for e in self.b.remote_list(str(self.root_a))[1]])

    def test_remote_stat(self):
        (self.root_b / "x.bin").write_bytes(b"1234")

        found = self.a.remote_stat(str(self.root_b / "x.bin"))
        self.assertTrue(found["exists"])
        self.assertFalse(found["is_dir"])
        self.assertEqual(found["size"], 4)

        missing = self.a.remote_stat(str(self.root_b / "nope"))
        self.assertFalse(missing["exists"])

    def test_remote_tree(self):
        (self.root_b / "d" / "e").mkdir(parents=True)
        (self.root_b / "d" / "one.txt").write_bytes(b"1")
        (self.root_b / "d" / "e" / "two.txt").write_bytes(b"22")

        files = self.a.remote_tree(str(self.root_b / "d"))
        self.assertEqual({f["path"] for f in files}, {"one.txt", "e/two.txt"})

    def test_path_escape_is_refused(self):
        with self.assertRaises(session.SessionError):
            self.a.remote_list("/etc")

    def test_pull_outside_root_is_refused(self):
        with self.assertRaises(session.SessionError):
            self.a.pull_file("/etc/hosts", self.root_a / "stolen")
        self.assertFalse((self.root_a / "stolen").exists())

    def test_pulling_a_directory_is_refused(self):
        (self.root_b / "adir").mkdir()
        with self.assertRaises(session.SessionError):
            self.a.pull_file(str(self.root_b / "adir"), self.root_a / "out")


class TestTransfers(SessionPairTestCase):
    def test_pull_small_file(self):
        src = self.root_b / "note.txt"
        src.write_bytes(b"MDirNet")

        got = self.a.pull_file(str(src), self.root_a / "note.txt")
        self.assertEqual(got, 7)
        self.assertEqual((self.root_a / "note.txt").read_bytes(), b"MDirNet")

    def test_pull_large_file_spans_many_chunks(self):
        src = self.root_b / "big.bin"
        src.write_bytes(bytes(range(256)) * 8192)  # 2 MiB

        seen = []
        dest = self.root_a / "big.bin"
        self.a.pull_file(str(src), dest, on_progress=seen.append)

        self.assertEqual(sha(src), sha(dest))
        self.assertGreater(len(seen), 1)  # 청크가 여러 개로 쪼개졌다
        self.assertEqual(sum(seen), src.stat().st_size)

    def test_push_file(self):
        src = self.root_a / "upload.bin"
        src.write_bytes(b"\x00\x01\x02" * 100000)

        dest = self.root_b / "sub" / "upload.bin"
        self.a.push_file(src, str(dest))
        self.assertEqual(sha(src), sha(dest))

    def test_push_outside_root_is_refused(self):
        src = self.root_a / "x.txt"
        src.write_bytes(b"x")
        with self.assertRaises(session.SessionError):
            self.a.push_file(src, "/tmp/mdirnet-should-not-exist.txt")

    def test_push_reports_received_event(self):
        src = self.root_a / "y.txt"
        src.write_bytes(b"y")
        self.a.push_file(src, str(self.root_b / "y.txt"))

        kinds = []
        while not self.events_b.empty():
            kinds.append(self.events_b.get_nowait()[0])
        self.assertIn("received", kinds)

    def test_no_part_file_is_left_behind(self):
        src = self.root_b / "f.bin"
        src.write_bytes(b"z" * 5000)
        self.a.pull_file(str(src), self.root_a / "f.bin")

        leftovers = [p.name for p in self.root_a.iterdir() if p.name.endswith(".part")]
        self.assertEqual(leftovers, [])

    def test_pull_overwrites_existing_destination(self):
        src = self.root_b / "v.txt"
        src.write_bytes(b"new content")
        dest = self.root_a / "v.txt"
        dest.write_bytes(b"old")

        self.a.pull_file(str(src), dest)
        self.assertEqual(dest.read_bytes(), b"new content")

    def test_recursive_directory_pull(self):
        """tree 로 목록을 받아 파일별로 내려받으면 구조가 그대로 재현된다."""
        base = self.root_b / "project"
        (base / "src" / "deep").mkdir(parents=True)
        (base / "README.md").write_bytes(b"# hi")
        (base / "src" / "main.py").write_bytes(b"print(1)\n" * 500)
        (base / "src" / "deep" / "blob.bin").write_bytes(bytes(range(256)) * 300)

        dest_root = self.root_a / "project"
        for item in self.a.remote_tree(str(base)):
            self.a.pull_file(str(base / item["path"]), dest_root / item["path"])

        self.assertEqual(
            {f["path"] for f in fsops.walk_tree(base)},
            {f["path"] for f in fsops.walk_tree(dest_root)},
        )
        for item in fsops.walk_tree(base):
            self.assertEqual(sha(base / item["path"]), sha(dest_root / item["path"]))

    def test_concurrent_pull_and_push_do_not_mix_up(self):
        """양쪽이 동시에 전송해도 id 가 섞이지 않아야 한다.

        두 노드 모두 요청 id 를 1부터 매기므로, 'q' 표시로 대화를 구분하지 못하면
        내 pull 응답과 상대의 push 데이터가 같은 우편함으로 흘러들어 깨진다.
        """
        # a 가 b 에게서 받아오는 파일
        b_file = self.root_b / "down.bin"
        b_file.write_bytes(b"D" * 900_000)
        # 동시에 b 가 a 에게 밀어넣는 파일. 목적지는 받는 쪽(a)의 루트 안이어야 한다
        b_upload = self.root_b / "up.bin"
        b_upload.write_bytes(b"U" * 900_000)
        a_dest = self.root_a / "up-from-b.bin"

        errors = []

        def pull():
            try:
                self.a.pull_file(str(b_file), self.root_a / "down.bin")
            except Exception as exc:  # noqa: BLE001 - 테스트에서 원인을 그대로 보고한다
                errors.append(("pull", exc))

        def push():
            try:
                self.b.push_file(b_upload, str(a_dest))
            except Exception as exc:  # noqa: BLE001
                errors.append(("push", exc))

        threads = [threading.Thread(target=pull), threading.Thread(target=push)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(sha(b_file), sha(self.root_a / "down.bin"))
        self.assertEqual(a_dest.read_bytes(), b"U" * 900_000)


class TestApprovalHandshake(unittest.TestCase):
    """실제 TCP 로 Server 를 띄워 요청 → 승인/거부 흐름을 확인한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.events = queue.Queue()

        self.server = session.Server("host-b", "linux", self.root, self.events, port=0)
        self.port = self.server.bind()
        self.server.start()
        self.addCleanup(self.server.stop)

    def peer(self):
        return discovery_peer("host-b", "127.0.0.1", self.port)

    def connect_in_thread(self, out):
        def run():
            try:
                out.append(
                    session.connect(self.peer(), "host-a", "darwin", self.root, queue.Queue())
                )
            except Exception as exc:  # noqa: BLE001
                out.append(exc)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def next_approval(self):
        kind, payload = self.events.get(timeout=10)
        self.assertEqual(kind, "approval")
        return payload

    def test_accepted_connection_yields_working_session(self):
        (self.root / "shared.txt").write_bytes(b"visible")

        out = []
        thread = self.connect_in_thread(out)

        approval = self.next_approval()
        self.assertEqual(approval.name, "host-a")
        self.assertEqual(approval.platform, "darwin")
        approval.decide(True)

        thread.join(timeout=10)
        self.assertEqual(len(out), 1, "connect 가 끝나지 않았습니다")
        client = out[0]
        self.assertIsInstance(client, session.Session)
        self.addCleanup(client.close)

        # 서버 쪽도 connected 이벤트를 올렸는지 확인한다
        kinds = []
        for _ in range(3):
            try:
                kinds.append(self.events.get(timeout=5)[0])
            except queue.Empty:
                break
        self.assertIn("connected", kinds)

        _, entries = client.remote_list(str(self.root))
        self.assertIn("shared.txt", [e["name"] for e in entries])

    def test_rejected_connection_raises(self):
        out = []
        thread = self.connect_in_thread(out)

        self.next_approval().decide(False)

        thread.join(timeout=10)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], session.Rejected)

    def test_version_mismatch_is_refused_before_approval(self):
        from mdirnet import protocol

        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(sock.close)
        protocol.send_frame(sock, {"t": "hello", "v": 999, "name": "old-client"})

        header, _ = protocol.FrameReader(sock).read()
        self.assertEqual(header["t"], "hello.no")
        self.assertTrue(self.events.empty(), "승인을 물어보면 안 된다")


class _FakePeer:
    def __init__(self, name, host, port):
        self.name, self.host, self.port = name, host, port


def discovery_peer(name, host, port):
    return _FakePeer(name, host, port)


if __name__ == "__main__":
    unittest.main()
