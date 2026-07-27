# 두 App 인스턴스를 실제로 띄워 발견 → 승인 → 재귀 복사까지 통과시키는 테스트

import hashlib
import tempfile
import time
import unittest
from pathlib import Path

from mdirnet import fsops
from mdirnet.ui import app as app_module


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pump(apps, seconds, until=None):
    """UI 루프 대신 이벤트만 흘려준다. until 이 참이 되면 일찍 끝낸다."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for app in apps:
            app._drain_events()
        if until and until():
            return True
        time.sleep(0.02)
    return bool(until and until())


class TestTwoNodes(unittest.TestCase):
    """터미널 없이 App 두 개를 세운다. 실제 TCP·UDP 를 쓴다."""

    UDP_PORT = 9711  # 테스트 전용 포트. 실사용 9601 과 섞이지 않게 한다

    def setUp(self):
        self.dir_a = tempfile.TemporaryDirectory()
        self.dir_b = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir_a.cleanup)
        self.addCleanup(self.dir_b.cleanup)
        self.root_a = Path(self.dir_a.name).resolve()
        self.root_b = Path(self.dir_b.name).resolve()

        self.a = self._make("node-a", self.root_a)
        self.b = self._make("node-b", self.root_b)

    def _make(self, name, root):
        app = app_module.App(
            my_name=name,
            root=root,
            tcp_port=0,
            udp_port=self.UDP_PORT,
            platform_name="testos",
            use_discovery=True,
        )
        app.start_services()
        app.local.refresh()
        self.addCleanup(app.shutdown)
        return app

    def _connect(self):
        """a 가 b 를 발견해 연결을 요청하고, b 가 승인한다."""
        found = pump([self.a, self.b], 8, until=lambda: self._peer_of(self.a, "node-b"))
        self.assertTrue(found, "node-a 가 node-b 를 발견하지 못했습니다")

        self.a.request_connection(self._peer_of(self.a, "node-b"))

        # b 쪽에 승인 창이 떠야 한다
        got = pump(
            [self.a, self.b],
            8,
            until=lambda: isinstance(self.b.modal, app_module.ApprovalDialog),
        )
        self.assertTrue(got, "승인 창이 뜨지 않았습니다")
        self.assertEqual(self.b.modal.request.name, "node-a")

        self.b._on_key("y")

        ready = pump(
            [self.a, self.b],
            8,
            until=lambda: self.a.session and self.b.session and self.a.remote.entries is not None,
        )
        self.assertTrue(ready, "세션이 수립되지 않았습니다")

    def _peer_of(self, app, name):
        return next((p for p in app.discovery.peers() if p.name == name), None)

    def test_discovery_finds_the_other_node(self):
        found = pump([self.a, self.b], 8, until=lambda: self._peer_of(self.b, "node-a"))
        self.assertTrue(found)
        peer = self._peer_of(self.b, "node-a")
        self.assertEqual(peer.port, self.a.port)

    def test_rejecting_leaves_both_unconnected(self):
        pump([self.a, self.b], 8, until=lambda: self._peer_of(self.a, "node-b"))
        self.a.request_connection(self._peer_of(self.a, "node-b"))
        pump([self.a, self.b], 8, until=lambda: isinstance(self.b.modal, app_module.ApprovalDialog))

        self.b._on_key("n")

        pump([self.a, self.b], 5, until=lambda: self.a.modal is not None)
        self.assertIsNone(self.a.session)
        self.assertIsNone(self.b.session)
        self.assertIsInstance(self.a.modal, app_module.MessageDialog)
        self.assertIn("거부", "\n".join(self.a.modal.lines(60)))

    def test_approval_then_both_sides_see_each_other(self):
        (self.root_a / "only-on-a.txt").write_bytes(b"a")
        (self.root_b / "only-on-b.txt").write_bytes(b"b")
        self.a.local.refresh()
        self.b.local.refresh()

        self._connect()
        pump([self.a, self.b], 5, until=lambda: self.a.remote.entries)

        self.assertIn("only-on-b.txt", [e["name"] for e in self.a.remote.entries])

        # 승인한 쪽도 상대를 볼 수 있어야 한다 (대칭 세션)
        self.b.remote.path = self.root_a
        self.b.remote.refresh()
        pump([self.a, self.b], 5, until=lambda: self.b.remote.entries)
        self.assertIn("only-on-a.txt", [e["name"] for e in self.b.remote.entries])

    def test_push_a_directory_recursively(self):
        """a 의 디렉터리를 b 로 밀어넣는다. 구조와 내용이 그대로 재현되어야 한다."""
        project = self.root_a / "project"
        (project / "src" / "deep").mkdir(parents=True)
        (project / "README.md").write_bytes(b"# MDirNet\n")
        (project / "src" / "main.py").write_bytes(b"print('hi')\n" * 2000)
        (project / "src" / "deep" / "blob.bin").write_bytes(bytes(range(256)) * 1500)
        (project / "한글 폴더").mkdir()
        (project / "한글 폴더" / "메모.txt").write_bytes("안녕하세요".encode("utf-8"))
        self.a.local.refresh()

        self._connect()
        pump([self.a, self.b], 5, until=lambda: self.a.remote.entries is not None)

        # 커서를 project 에 두고 F5 → 확인 창에서 Y
        self.a.local.cursor = next(
            i for i, e in enumerate(self.a.local.rows()) if e["name"] == "project"
        )
        self.a._on_key("F5")
        self.assertIsInstance(self.a.modal, app_module.ConfirmDialog)
        self.a._on_key("y")

        done = pump(
            [self.a, self.b], 60, until=lambda: self.a.transfer is None and self.a.modal is None
        )
        self.assertTrue(done, "전송이 끝나지 않았습니다 (status=%s)" % self.a.status)

        dest = self.root_b / "project"
        self.assertEqual(
            {f["path"] for f in fsops.walk_tree(project)},
            {f["path"] for f in fsops.walk_tree(dest)},
        )
        for item in fsops.walk_tree(project):
            self.assertEqual(sha(project / item["path"]), sha(dest / item["path"]), item["path"])

        leftovers = [p.name for p in dest.rglob("*.part")]
        self.assertEqual(leftovers, [])

    def test_pull_selected_files_from_remote(self):
        """b 에 있는 여러 파일을 골라 a 로 받아온다."""
        (self.root_b / "one.txt").write_bytes(b"1" * 1000)
        (self.root_b / "two.bin").write_bytes(b"2" * 300_000)
        (self.root_b / "skip.txt").write_bytes(b"no")
        self.b.local.refresh()

        self._connect()
        pump([self.a, self.b], 5, until=lambda: len(self.a.remote.entries) >= 3)

        self.a.focus = self.a.remote
        self.a.remote.selected = {"one.txt", "two.bin"}

        self.a._on_key("F5")
        self.assertIsInstance(self.a.modal, app_module.ConfirmDialog)
        self.a._on_key("y")

        done = pump(
            [self.a, self.b], 60, until=lambda: self.a.transfer is None and self.a.modal is None
        )
        self.assertTrue(done, "전송이 끝나지 않았습니다 (status=%s)" % self.a.status)

        self.assertEqual(sha(self.root_b / "one.txt"), sha(self.root_a / "one.txt"))
        self.assertEqual(sha(self.root_b / "two.bin"), sha(self.root_a / "two.bin"))
        self.assertFalse((self.root_a / "skip.txt").exists())

    def test_disconnect_clears_remote_pane_on_both_sides(self):
        self._connect()
        pump([self.a, self.b], 5, until=lambda: self.a.remote.entries is not None)

        self.a._on_key("F6")  # F6 = 연결 끊기
        gone = pump(
            [self.a, self.b], 8, until=lambda: not self.a.session and not self.b.session
        )
        self.assertTrue(gone, "연결이 정리되지 않았습니다")
        self.assertEqual(self.a.remote.entries, [])
        self.assertEqual(self.b.remote.entries, [])


if __name__ == "__main__":
    unittest.main()
