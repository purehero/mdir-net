# 이동 - MDir 에 있던 파일 관리 기능 중 빠져 있던 것
#
# 이동이 위험한 지점은 하나뿐이다. **복사가 반쯤 실패했는데 원본을 지우는 것.**
# 양쪽에 남는 것은 사용자가 고칠 수 있지만 사라진 것은 고칠 수 없다.
# 그래서 여기서는 잘 되는 경우보다 **실패했을 때 원본이 남는가**를 더 본다.

import queue
import tempfile
import unittest
from pathlib import Path

from mdirnet import fsops
from mdirnet.ui import app as app_module

from .helpers import make_shares


class TestRenameWithinDisk(unittest.TestCase):
    """같은 디스크면 복사하지 않고 이름만 바꾼다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_moves_a_whole_tree_at_once(self):
        (self.root / "a" / "안쪽").mkdir(parents=True)
        (self.root / "a" / "안쪽" / "메모.txt").write_text("내용", encoding="utf-8")

        self.assertTrue(fsops.rename_within_disk(self.root / "a", self.root / "b"))

        self.assertFalse((self.root / "a").exists())
        self.assertEqual(
            (self.root / "b" / "안쪽" / "메모.txt").read_text(encoding="utf-8"), "내용"
        )

    def test_refuses_to_overwrite(self):
        """os.rename 은 파일이면 조용히 덮어쓴다. 그 전에 막는다."""
        (self.root / "a").write_text("원본", encoding="utf-8")
        (self.root / "b").write_text("건드리면 안 되는 것", encoding="utf-8")

        self.assertFalse(fsops.rename_within_disk(self.root / "a", self.root / "b"))

        self.assertEqual((self.root / "a").read_text(encoding="utf-8"), "원본")
        self.assertEqual(
            (self.root / "b").read_text(encoding="utf-8"), "건드리면 안 되는 것"
        )

    def test_missing_source_is_false_not_an_exception(self):
        self.assertFalse(fsops.rename_within_disk(self.root / "없음", self.root / "b"))


class MoveAppTestCase(unittest.TestCase):
    """왼쪽(내 PC)과 오른쪽(내 PC)을 서로 다른 폴더로 두고 이동을 시킨다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

        self.here = self.root / "여기"
        self.there = self.root / "저기"
        self.here.mkdir()
        self.there.mkdir()

        (self.here / "보고서.txt").write_bytes(b"x" * 100)
        (self.here / "폴더").mkdir()
        (self.here / "폴더" / "안.txt").write_bytes(b"y" * 50)

        self.app = app_module.App(
            my_name="move-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)

        # 오른쪽을 내 PC 로 돌려 두 로컬 패널 사이에서 옮긴다
        self.app.toggle_right_source()
        self.app.left.navigate(self.here)
        self.app.right.navigate(self.there)

    def pick(self, *names):
        self.app.focus = self.app.left
        self.app.left.selected = set(names)

    def confirm(self):
        """확인 창의 '예' 를 누른다."""
        self.assertIsNotNone(self.app.modal, "확인 창이 뜨지 않았습니다")
        self.app.modal.decide(True)
        self.app.modal = None

    def drain(self):
        while True:
            try:
                self.app.events.get_nowait()
            except queue.Empty:
                return


class TestLocalMove(MoveAppTestCase):
    def test_a_file_moves_and_the_source_is_gone(self):
        self.pick("보고서.txt")
        self.app._start_move()

        # 같은 디스크라 확인 창 없이 rename 으로 끝난다
        self.assertIsNone(self.app.modal, "같은 디스크인데 확인 창이 떴습니다")
        self.assertFalse((self.here / "보고서.txt").exists())
        self.assertTrue((self.there / "보고서.txt").exists())
        self.assertIn("1개", self.app.status)

    def test_a_folder_moves_with_everything_inside(self):
        self.pick("폴더")
        self.app._start_move()

        self.assertFalse((self.here / "폴더").exists())
        self.assertEqual((self.there / "폴더" / "안.txt").read_bytes(), b"y" * 50)

    def test_the_selection_is_cleared(self):
        """옮기고 나면 그 이름은 더 없다. 남겨두면 다음 동작이 없는 것을 가리킨다."""
        self.pick("보고서.txt")
        self.app._start_move()

        self.assertEqual(self.app.left.selected, set())

    def test_nothing_to_move_says_so(self):
        self.app.focus = self.app.left
        self.app.left.selected = set()
        self.app.left.cursor = 0  # '..'
        self.app._start_move()

        self.assertIn("고르세요", self.app.status)

    def test_the_same_pane_is_refused(self):
        self.app.focus = self.app.left
        self.app.right.navigate(self.here)
        self.pick("보고서.txt")

        # 같은 폴더를 보고 있어도 패널 객체가 다르면 통과한다. 여기서 막는 것은
        # 같은 **패널**을 원본이자 대상으로 삼는 경우다
        self.app.right = self.app.left
        self.app._start_move()

        self.assertIn("같은 패널", self.app.status)


class TestMoveKeepsTheSourceWhenItFails(MoveAppTestCase):
    """여기가 이 기능의 핵심이다."""

    def test_a_clashing_name_does_not_destroy_the_source(self):
        """대상에 같은 이름이 있으면 rename 이 거부된다. 원본은 그대로여야 한다."""
        (self.there / "보고서.txt").write_bytes("먼저 있던 것".encode("utf-8"))

        self.pick("보고서.txt")
        self.app._start_move()

        # rename 이 안 되므로 복사 + 삭제 경로로 넘어가 확인 창이 뜬다
        self.assertIsNotNone(self.app.modal, "덮어쓰기인데 확인 없이 진행했습니다")

        # 아직 아무것도 지워지지 않았다
        self.assertTrue((self.here / "보고서.txt").exists())
        self.assertEqual(
            (self.there / "보고서.txt").read_text(encoding="utf-8"), "먼저 있던 것"
        )

    def test_drop_sources_skips_items_that_failed(self):
        """전송에 실패한 항목의 원본은 남는다."""
        errors = []
        gone = app_module.App._drop_sources(
            self.app.left,
            [{"name": "보고서.txt", "is_dir": False}, {"name": "폴더", "is_dir": True}],
            failed={"폴더"},  # 이 항목은 전송 중 오류가 났다고 친다
            active=None,
            errors=errors,
        )

        self.assertEqual(gone, 1)
        self.assertFalse((self.here / "보고서.txt").exists())
        self.assertTrue((self.here / "폴더" / "안.txt").exists(), "실패한 원본이 지워졌다")
        self.assertEqual(errors, [])

    def test_a_source_that_cannot_be_removed_is_reported_not_swallowed(self):
        errors = []
        gone = app_module.App._drop_sources(
            self.app.left,
            [{"name": "없는것.txt", "is_dir": False}],
            failed=set(),
            active=None,
            errors=errors,
        )

        self.assertEqual(gone, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("원본을 지우지 못했습니다", errors[0])


if __name__ == "__main__":
    unittest.main()
