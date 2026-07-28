# 실행 권한이 있는 파일 위에서 Enter 를 눌렀을 때 실행되는지 확인하는 테스트

import os
import tempfile
import time
import unittest
from pathlib import Path

from mdirnet import fsops
from mdirnet.ui import app as app_module
from mdirnet.ui import term

from .helpers import make_shares


class RunFileTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

        self.script = self.root / "hello.sh"
        self.script.write_text("#!/bin/sh\necho hi\n")
        self.script.chmod(0o755)
        (self.root / "plain.txt").write_bytes("그냥 글자\n".encode("utf-8"))
        (self.root / "문서").mkdir()

        self.app = app_module.App(
            my_name="run-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.navigate(self.root)
        self.app.local.refresh()

        # 진짜 프로세스를 띄우지 않고 무엇을 실행하려 했는지만 받아둔다
        self.launched = []
        real_launch = fsops.launch
        fsops.launch = lambda path: self.launched.append(Path(path))
        self.addCleanup(lambda: setattr(fsops, "launch", real_launch))

    def cursor_to(self, name):
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == name
        )


@unittest.skipIf(os.name == "nt", "실행 권한은 POSIX 개념이다")
class TestIsExecutable(RunFileTestCase):
    def test_script_with_the_x_bit_is_executable(self):
        self.assertTrue(fsops.is_executable(self.script))

    def test_plain_file_is_not_executable(self):
        self.assertFalse(fsops.is_executable(self.root / "plain.txt"))

    def test_directory_is_not_executable(self):
        """디렉터리에는 x 비트가 있지만 실행 대상이 아니다 (macOS 의 .app 포함)."""
        self.assertFalse(fsops.is_executable(self.root / "문서"))

    def test_missing_path_is_not_executable(self):
        self.assertFalse(fsops.is_executable(self.root / "없음"))


@unittest.skipUnless(os.name == "nt", "PATHEXT 는 Windows 에서만 쓴다")
class TestIsExecutableOnWindows(RunFileTestCase):
    def test_extension_decides(self):
        exe = self.root / "tool.exe"
        exe.write_bytes(b"MZ")
        self.assertTrue(fsops.is_executable(exe))
        self.assertFalse(fsops.is_executable(self.root / "plain.txt"))


@unittest.skipIf(os.name == "nt", "실행 권한은 POSIX 개념이다")
class TestEnterRuns(RunFileTestCase):
    def test_enter_on_an_executable_runs_it(self):
        self.cursor_to("hello.sh")
        self.app._on_key(term.ENTER)

        self.assertEqual(self.launched, [self.script])
        self.assertIsNone(self.app.modal, "실행에는 창이 뜨지 않는다")
        self.assertIn("hello.sh", self.app.status)
        self.assertIn("실행", self.app.status)

    def test_enter_on_a_plain_file_still_views_it(self):
        self.cursor_to("plain.txt")
        self.app._on_key(term.ENTER)

        self.assertEqual(self.launched, [])
        self.assertIsInstance(self.app.modal, app_module.ViewerDialog)

    def test_enter_on_a_directory_still_navigates(self):
        self.cursor_to("문서")
        self.app._on_key(term.ENTER)

        self.assertEqual(self.launched, [])
        self.assertEqual(Path(self.app.local.path), self.root / "문서")

    def test_f3_views_an_executable_instead_of_running_it(self):
        """보기는 보기다. 실행은 Enter 만 한다."""
        self.cursor_to("hello.sh")
        self.app._on_key("F3")

        self.assertEqual(self.launched, [])
        self.assertIsInstance(self.app.modal, app_module.ViewerDialog)

    def test_failure_to_launch_is_reported(self):
        def boom(path):
            raise OSError("Exec format error")

        fsops.launch = boom
        self.cursor_to("hello.sh")
        self.app._on_key(term.ENTER)

        self.assertIsInstance(self.app.modal, app_module.MessageDialog)
        self.assertIn("Exec format error", "\n".join(self.app.modal.lines(70)))

    def test_remote_file_is_not_executed(self):
        """상대 파일은 실행 권한을 알 수 없다. 지금까지처럼 보기로 둔다."""
        viewed = []
        self.app.focus = self.app.remote
        self.app.remote.current_item = lambda: {
            "name": "hello.sh", "is_dir": False, "size": 1
        }
        self.app._view_file = lambda: viewed.append(True)

        self.app._enter()

        self.assertEqual(self.launched, [])
        self.assertEqual(viewed, [True])


@unittest.skipIf(os.name == "nt", "sh 스크립트를 쓴다")
class TestLaunchReallyRuns(unittest.TestCase):
    """`fsops.launch` 가 실제로 프로세스를 띄우는지 한 번은 확인한다."""

    def test_launched_script_leaves_its_mark(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()

        marker = root / "지나갔다"
        script = root / "touch.sh"
        script.write_text("#!/bin/sh\ntouch '%s'\n" % marker)
        script.chmod(0o755)

        fsops.launch(script)

        for _ in range(100):  # 최대 5초. 백그라운드라 곧바로 끝나지 않는다
            if marker.exists():
                break
            time.sleep(0.05)
        self.assertTrue(marker.exists(), "실행된 스크립트의 결과가 없다")

    def test_finished_children_are_reaped(self):
        """거두지 않으면 끝난 자식이 좀비로 쌓인다."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()

        script = root / "quick.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)

        fsops.launch(script)
        first = fsops._launched[-1]
        first.wait(timeout=5)

        fsops.launch(script)  # 이때 앞의 자식을 거둔다
        self.assertNotIn(first, fsops._launched)


if __name__ == "__main__":
    unittest.main()
