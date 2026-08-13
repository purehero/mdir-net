# 승인 핸드셰이크와 양방향 파일/디렉터리 전송을 루프백으로 확인하는 테스트
#
# 원격 경로는 호스트 절대 경로가 아니라 공유 상대 경로다.
#   "."                  가상 루트 (공유 목록)
#   "<공유이름>"          공유 폴더 하나
#   "<공유이름>/a/b.txt"  그 안의 파일

import hashlib
import queue
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from mdirnet import fsops, session, shares as shares_mod

from .helpers import make_shares, only_name, rpath


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

        self.shares_a = make_shares(self.root_a)
        self.shares_b = make_shares(self.root_b)
        # 원격 경로의 첫 조각. 상대가 이 이름으로 내 공유 폴더를 가리킨다
        self.ra = only_name(self.shares_a)
        self.rb = only_name(self.shares_b)

        sock_a, sock_b = socket.socketpair()
        self.events_a, self.events_b = queue.Queue(), queue.Queue()
        self.a = session.Session(sock_a, "node-b", "127.0.0.1", self.shares_a, self.events_a)
        self.b = session.Session(sock_b, "node-a", "127.0.0.1", self.shares_b, self.events_b)
        self.a.start()
        self.b.start()
        self.addCleanup(self.b.close)
        self.addCleanup(self.a.close)


class TestVirtualRoot(SessionPairTestCase):
    def test_home_is_the_virtual_root(self):
        """상대에게 내 호스트 경로를 알려주지 않는다."""
        self.assertEqual(self.a.remote_home(), shares_mod.ROOT)

    def test_root_lists_share_names_as_directories(self):
        path, entries = self.a.remote_list(shares_mod.ROOT)
        self.assertEqual(path, shares_mod.ROOT)
        self.assertEqual([e["name"] for e in entries], [self.rb])
        self.assertTrue(entries[0]["is_dir"])

    def test_multiple_shares_all_appear_at_the_root(self):
        extra = self.root_b.parent / "extra-share"
        extra.mkdir()
        self.addCleanup(extra.rmdir)
        self.shares_b.add(extra)

        _, entries = self.a.remote_list(shares_mod.ROOT)
        self.assertEqual({e["name"] for e in entries}, {self.rb, "extra-share"})

    def test_missing_share_is_hidden_from_the_root(self):
        """외장 디스크를 뺀 경우. 들어가도 실패할 항목을 보여주지 않는다."""
        gone = self.root_b.parent / "unplugged"
        gone.mkdir()
        self.shares_b.add(gone)
        gone.rmdir()

        _, entries = self.a.remote_list(shares_mod.ROOT)
        self.assertEqual([e["name"] for e in entries], [self.rb])

    def test_root_stat_says_it_is_a_directory(self):
        reply = self.a.remote_stat(shares_mod.ROOT)
        self.assertTrue(reply["exists"])
        self.assertTrue(reply["is_dir"])

    def test_root_cannot_be_pulled_as_a_file(self):
        with self.assertRaises(session.SessionError):
            self.a.pull_file(shares_mod.ROOT, self.root_a / "nope")

    def test_root_cannot_be_deleted(self):
        with self.assertRaises(session.SessionError):
            self.a.remote_delete(shares_mod.ROOT)
        self.assertTrue(self.root_b.is_dir())

    def test_cannot_mkdir_at_the_root(self):
        with self.assertRaises(session.SessionError):
            self.a.remote_mkdir(shares_mod.ROOT)


class TestQueries(SessionPairTestCase):
    def test_remote_list_inside_a_share(self):
        (self.root_b / "docs").mkdir()
        (self.root_b / "note.txt").write_bytes(b"hello")

        path, entries = self.a.remote_list(self.rb)
        self.assertEqual(path, self.rb)
        self.assertEqual([e["name"] for e in entries], ["docs", "note.txt"])
        self.assertEqual(entries[1]["size"], 5)

    def test_remote_list_of_a_subdirectory(self):
        (self.root_b / "docs" / "deep").mkdir(parents=True)
        (self.root_b / "docs" / "f.txt").write_bytes(b"x")

        path, entries = self.a.remote_list(rpath(self.rb, "docs"))
        self.assertEqual(path, rpath(self.rb, "docs"))
        self.assertEqual([e["name"] for e in entries], ["deep", "f.txt"])

    def test_both_directions_work(self):
        """대칭 세션이므로 승인한 쪽도 상대를 조회할 수 있어야 한다."""
        (self.root_a / "from_a.txt").write_bytes(b"a")
        (self.root_b / "from_b.txt").write_bytes(b"b")

        self.assertIn("from_b.txt", [e["name"] for e in self.a.remote_list(self.rb)[1]])
        self.assertIn("from_a.txt", [e["name"] for e in self.b.remote_list(self.ra)[1]])

    def test_remote_stat(self):
        (self.root_b / "x.bin").write_bytes(b"1234")

        found = self.a.remote_stat(rpath(self.rb, "x.bin"))
        self.assertTrue(found["exists"])
        self.assertFalse(found["is_dir"])
        self.assertEqual(found["size"], 4)

        missing = self.a.remote_stat(rpath(self.rb, "nope"))
        self.assertFalse(missing["exists"])

    def test_remote_tree_returns_paths_relative_to_the_request(self):
        (self.root_b / "d" / "e").mkdir(parents=True)
        (self.root_b / "d" / "one.txt").write_bytes(b"1")
        (self.root_b / "d" / "e" / "two.txt").write_bytes(b"22")

        files, dirs = self.a.remote_tree(rpath(self.rb, "d"))
        self.assertEqual({f["path"] for f in files}, {"one.txt", "e/two.txt"})
        self.assertEqual(dirs, ["e"])

    def test_remote_tree_reports_empty_directories(self):
        """파일이 하나도 없는 폴더도 알려줘야 복사할 때 만들 수 있다."""
        (self.root_b / "d" / "빈폴더" / "더깊은빈폴더").mkdir(parents=True)

        files, dirs = self.a.remote_tree(rpath(self.rb, "d"))
        self.assertEqual(files, [])
        self.assertEqual(dirs, ["빈폴더", "빈폴더/더깊은빈폴더"])


class TestPathSafety(SessionPairTestCase):
    def test_unknown_share_name_is_refused(self):
        with self.assertRaises(session.SessionError):
            self.a.remote_list("없는공유")

    def test_absolute_host_path_is_refused(self):
        """이제 절대 경로는 첫 조각이 공유 이름으로 해석되어 걸린다."""
        with self.assertRaises(session.SessionError):
            self.a.remote_list("/etc")

    def test_dotdot_stops_at_the_virtual_root(self):
        """`..` 을 아무리 쌓아도 가상 루트까지만 올라간다. 그 위는 없다."""
        path, entries = self.a.remote_list(rpath(self.rb, "..", "..", ".."))
        self.assertEqual(path, shares_mod.ROOT)
        self.assertEqual([e["name"] for e in entries], [self.rb])

    def test_dotdot_is_resolved_not_stripped(self):
        """`공유/a/../b` 는 `공유/b` 여야 한다. `..` 을 그냥 버리면 `공유/a/b`
        가 되어 사용자 의도와 달라진다."""
        (self.root_b / "b").mkdir()
        (self.root_b / "b" / "found.txt").write_bytes(b"x")

        path, entries = self.a.remote_list(rpath(self.rb, "a", "..", "b"))
        self.assertEqual(path, rpath(self.rb, "b"))
        self.assertEqual([e["name"] for e in entries], ["found.txt"])

    def test_pull_outside_a_share_is_refused(self):
        with self.assertRaises(session.SessionError):
            self.a.pull_file("/etc/hosts", self.root_a / "stolen")
        self.assertFalse((self.root_a / "stolen").exists())

    def test_pulling_a_directory_is_refused(self):
        (self.root_b / "adir").mkdir()
        with self.assertRaises(session.SessionError):
            self.a.pull_file(rpath(self.rb, "adir"), self.root_a / "out")

    def test_push_outside_a_share_is_refused(self):
        src = self.root_a / "x.txt"
        src.write_bytes(b"x")
        with self.assertRaises(session.SessionError):
            self.a.push_file(src, "/tmp/mdirnet-should-not-exist.txt")

    def test_symlink_escape_is_refused(self):
        import os

        outside = Path(tempfile.mkdtemp())
        self.addCleanup(outside.rmdir)
        os.symlink(outside, self.root_b / "escape")

        with self.assertRaises(session.SessionError):
            self.a.remote_list(rpath(self.rb, "escape"))

    def test_share_folder_itself_cannot_be_deleted(self):
        with self.assertRaises(session.SessionError):
            self.a.remote_delete(self.rb)
        self.assertTrue(self.root_b.is_dir())

    def test_unexpected_handler_error_still_gets_a_reply(self):
        """핸들러가 예상 못한 예외로 죽어도 답이 와야 한다.

        답이 없으면 요청한 쪽이 30초 타임아웃까지 멈춰 있게 된다.
        """
        broken = self.root_b / "f.txt"
        broken.write_bytes(b"x")

        # _REQUESTS 는 함수 객체를 직접 담고 있으므로 클래스 속성을 바꿔도
        # 소용이 없다. 표를 직접 바꿔야 한다
        original = session._REQUESTS["stat"]

        def explode(self_, req_id, header, blob, box):
            raise RuntimeError("일부러 터뜨림")

        session._REQUESTS["stat"] = explode
        self.addCleanup(lambda: session._REQUESTS.__setitem__("stat", original))

        with self.assertRaises(session.SessionError) as caught:
            self.a.remote_stat(rpath(self.rb, "f.txt"))
        self.assertIn("일부러 터뜨림", str(caught.exception))


class TestTransfers(SessionPairTestCase):
    def test_pull_small_file(self):
        src = self.root_b / "note.txt"
        src.write_bytes(b"MDirNet")

        got = self.a.pull_file(rpath(self.rb, "note.txt"), self.root_a / "note.txt")
        self.assertEqual(got, 7)
        self.assertEqual((self.root_a / "note.txt").read_bytes(), b"MDirNet")

    def test_pull_large_file_spans_many_chunks(self):
        src = self.root_b / "big.bin"
        src.write_bytes(bytes(range(256)) * 8192)  # 2 MiB

        seen = []
        dest = self.root_a / "big.bin"
        self.a.pull_file(rpath(self.rb, "big.bin"), dest, on_progress=seen.append)

        self.assertEqual(sha(src), sha(dest))
        self.assertGreater(len(seen), 1)  # 청크가 여러 개로 쪼개졌다
        self.assertEqual(sum(seen), src.stat().st_size)

    def test_push_file(self):
        src = self.root_a / "upload.bin"
        src.write_bytes(b"\x00\x01\x02" * 100000)

        self.a.push_file(src, rpath(self.rb, "sub", "upload.bin"))
        self.assertEqual(sha(src), sha(self.root_b / "sub" / "upload.bin"))

    def test_push_reports_received_event(self):
        src = self.root_a / "y.txt"
        src.write_bytes(b"y")
        self.a.push_file(src, rpath(self.rb, "y.txt"))

        kinds = []
        while not self.events_b.empty():
            kinds.append(self.events_b.get_nowait()[0])
        self.assertIn("received", kinds)

    def test_no_part_file_is_left_behind(self):
        src = self.root_b / "f.bin"
        src.write_bytes(b"z" * 5000)
        self.a.pull_file(rpath(self.rb, "f.bin"), self.root_a / "f.bin")

        leftovers = [p.name for p in self.root_a.iterdir() if p.name.endswith(".part")]
        self.assertEqual(leftovers, [])

    def test_pull_overwrites_existing_destination(self):
        (self.root_b / "v.txt").write_bytes(b"new content")
        dest = self.root_a / "v.txt"
        dest.write_bytes(b"old")

        self.a.pull_file(rpath(self.rb, "v.txt"), dest)
        self.assertEqual(dest.read_bytes(), b"new content")

    def test_recursive_directory_pull(self):
        """tree 로 목록을 받아 파일별로 내려받으면 구조가 그대로 재현된다."""
        base = self.root_b / "project"
        (base / "src" / "deep").mkdir(parents=True)
        (base / "README.md").write_bytes(b"# hi")
        (base / "src" / "main.py").write_bytes(b"print(1)\n" * 500)
        (base / "src" / "deep" / "blob.bin").write_bytes(bytes(range(256)) * 300)

        dest_root = self.root_a / "project"
        remote_base = rpath(self.rb, "project")
        for item in self.a.remote_tree(remote_base)[0]:
            self.a.pull_file(rpath(remote_base, item["path"]), dest_root / item["path"])

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
        (self.root_b / "down.bin").write_bytes(b"D" * 900_000)
        b_upload = self.root_b / "up.bin"
        b_upload.write_bytes(b"U" * 900_000)

        errors = []

        def pull():
            try:
                self.a.pull_file(rpath(self.rb, "down.bin"), self.root_a / "down.bin")
            except Exception as exc:  # noqa: BLE001 - 테스트에서 원인을 그대로 보고한다
                errors.append(("pull", exc))

        def push():
            try:
                self.b.push_file(b_upload, rpath(self.ra, "up-from-b.bin"))
            except Exception as exc:  # noqa: BLE001
                errors.append(("push", exc))

        threads = [threading.Thread(target=pull), threading.Thread(target=push)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(sha(self.root_b / "down.bin"), sha(self.root_a / "down.bin"))
        self.assertEqual((self.root_a / "up-from-b.bin").read_bytes(), b"U" * 900_000)

    def test_transfer_between_two_different_shares(self):
        """공유가 여러 개일 때 이름으로 올바른 폴더를 찾아야 한다."""
        second = self.root_b.parent / "second-share"
        second.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(second, ignore_errors=True))
        self.shares_b.add(second)
        (second / "in-second.txt").write_bytes(b"second!")

        self.a.pull_file(rpath("second-share", "in-second.txt"), self.root_a / "got.txt")
        self.assertEqual((self.root_a / "got.txt").read_bytes(), b"second!")


class TestClosedSession(SessionPairTestCase):
    def test_request_after_close_raises_session_error_not_oserror(self):
        """닫힌 소켓에 쓰면 OSError 가 난다. 그대로 새어나가면 워커 스레드에서
        트레이스백이 터져 TUI 화면을 뭉갠다. SessionError 로 바뀌어야 한다."""
        self.a.close("테스트")
        with self.assertRaises(session.SessionError):
            self.a.remote_list(self.rb)

    def test_pull_after_close_raises_session_error(self):
        self.a.close("테스트")
        with self.assertRaises(session.SessionError):
            self.a.pull_file(rpath(self.rb, "whatever"), self.root_a / "x")

    def test_push_after_close_raises_session_error(self):
        src = self.root_a / "p.txt"
        src.write_bytes(b"p")
        self.a.close("테스트")
        with self.assertRaises(session.SessionError):
            self.a.push_file(src, rpath(self.rb, "p.txt"))

    def test_close_is_idempotent(self):
        self.a.close("한번")
        self.a.close("두번")
        self.assertTrue(self.a.closed)

    def test_close_reports_disconnect_event_once(self):
        self.a.close("사유")
        self.a.close("또 사유")
        kinds = []
        while not self.events_a.empty():
            kinds.append(self.events_a.get_nowait())
        self.assertEqual([k for k, *_ in kinds].count("disconnected"), 1)


class TestApprovalHandshake(unittest.TestCase):
    """실제 TCP 로 Server 를 띄워 요청 → 승인/거부 흐름을 확인한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.events = queue.Queue()
        self.shares = make_shares(self.root)
        self.share_name = only_name(self.shares)

        self.server = session.Server("host-b", "linux", self.shares, self.events, port=0)
        self.port = self.server.bind()
        self.server.start()
        self.addCleanup(self.server.stop)

    def peer(self):
        return _FakePeer("host-b", "127.0.0.1", self.port)

    def connect_in_thread(self, out):
        def run():
            try:
                out.append(
                    session.connect(
                        self.peer(), "host-a", "darwin", make_shares(self.root), queue.Queue()
                    )
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

        kinds = []
        for _ in range(3):
            try:
                kinds.append(self.events.get(timeout=5)[0])
            except queue.Empty:
                break
        self.assertIn("connected", kinds)

        # 가상 루트에 공유 이름이 보이고, 그 안에 파일이 있어야 한다
        _, roots = client.remote_list(shares_mod.ROOT)
        self.assertEqual([e["name"] for e in roots], [self.share_name])
        _, entries = client.remote_list(self.share_name)
        self.assertIn("shared.txt", [e["name"] for e in entries])

    def test_rejected_connection_raises(self):
        out = []
        thread = self.connect_in_thread(out)

        self.next_approval().decide(False)

        thread.join(timeout=10)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], session.Rejected)

    def test_version_mismatch_is_refused_before_approval(self):
        from mdirnet import protocol, secure

        raw = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        # 선은 이제 늘 TLS 다. 평문으로 붙으면 프레임을 읽어보기도 전에 끊긴다
        sock = secure.wrap_client(raw)
        self.addCleanup(sock.close)
        protocol.send_frame(sock, {"t": "hello", "v": 999, "name": "old-client"})

        header, _ = protocol.FrameReader(sock).read()
        self.assertEqual(header["t"], "hello.no")
        self.assertTrue(self.events.empty(), "승인을 물어보면 안 된다")


class _FakePeer:
    def __init__(self, name, host, port):
        self.name, self.host, self.port = name, host, port


if __name__ == "__main__":
    unittest.main()
