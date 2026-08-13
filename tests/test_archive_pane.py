# 압축을 폴더처럼 다루는 패널 - 들어가기, 나오기, 보기, 꺼내기
#
# 압축은 **읽기 전용**이다. 안에 넣거나 압축 안의 것을 옮기는 길이 열려 있으면
# 사용자는 되는 줄 알고 시도하고, 그때 무슨 일이 일어날지는 아무도 모른다.
# 그래서 막히는 것들을 확인하는 데 무게를 뒀다.

import queue
import tempfile
import unittest
import zipfile
from pathlib import Path

from mdirnet.ui import app as app_module

from .helpers import make_shares


class ArchivePaneTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

        self.here = self.root / "여기"
        self.there = self.root / "저기"
        self.here.mkdir()
        self.there.mkdir()

        self.box = self.here / "묶음.zip"
        with zipfile.ZipFile(self.box, "w") as zf:
            zf.writestr("읽어보세요.txt", b"top")
            zf.writestr("안쪽/메모.txt", b"inside")
            zf.writestr("안쪽/더안쪽/깊은것.bin", b"z" * 64)

        self.app = app_module.App(
            my_name="zip-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.toggle_right_source()  # 오른쪽도 내 PC 로
        self.app.left.navigate(self.here)
        self.app.right.navigate(self.there)
        self.app.focus = self.app.left

    def cursor_to(self, name):
        rows = self.app.focus.rows()
        for index, row in enumerate(rows):
            if row["name"] == name:
                self.app.focus.cursor = index
                return
        self.fail("%s 가 목록에 없습니다: %s" % (name, [r["name"] for r in rows]))

    def names(self, pane=None):
        pane = pane or self.app.focus
        return {e["name"] for e in pane.entries}

    def drain(self):
        while True:
            try:
                self.app._drain_events()
                return
            except queue.Empty:
                return


class TestGoingInAndOut(ArchivePaneTestCase):
    def test_enter_opens_the_archive_like_a_folder(self):
        self.cursor_to("묶음.zip")
        self.app._enter()

        self.assertIsNotNone(self.app.left.archive)
        self.assertEqual(self.names(), {"읽어보세요.txt", "안쪽"})

    def test_going_deeper_inside(self):
        self.cursor_to("묶음.zip")
        self.app._enter()
        self.cursor_to("안쪽")
        self.app._enter()

        self.assertEqual(self.names(), {"메모.txt", "더안쪽"})
        self.assertEqual(self.app.left.archive[1], "안쪽")

    def test_up_climbs_inside_then_leaves(self):
        self.cursor_to("묶음.zip")
        self.app._enter()
        self.cursor_to("안쪽")
        self.app._enter()

        self.app._go_up(self.app.left)
        self.assertEqual(self.app.left.archive[1], "")  # 압축 맨 위

        self.app._go_up(self.app.left)
        self.assertIsNone(self.app.left.archive, "압축 밖으로 못 나왔다")
        self.assertEqual(self.app.left.path, self.here)

    def test_the_location_shows_where_you_are(self):
        self.cursor_to("묶음.zip")
        self.app._enter()
        self.cursor_to("안쪽")
        self.app._enter()

        self.assertIn("묶음.zip", self.app.left.location)
        self.assertTrue(self.app.left.location.endswith("안쪽"), self.app.left.location)

    def test_there_is_always_a_way_out(self):
        """압축 맨 위에서도 `..` 이 보여야 한다. 없으면 갇힌다."""
        self.cursor_to("묶음.zip")
        self.app._enter()

        self.assertFalse(self.app.left.at_top)
        self.assertEqual(self.app.left.rows()[0]["name"], "..")

    def test_a_file_that_only_looks_like_an_archive_is_not_entered(self):
        fake = self.here / "가짜.zip"
        fake.write_bytes(b"not a zip")
        self.app.left.refresh()
        self.cursor_to("가짜.zip")

        self.app._enter()

        # 압축으로 못 열면 평소대로 보기가 뜬다
        self.assertIsNone(self.app.left.archive)
        self.assertEqual(type(self.app.modal).__name__, "ViewerDialog")


class TestViewingInside(ArchivePaneTestCase):
    def test_view_reads_from_the_archive(self):
        self.cursor_to("묶음.zip")
        self.app._enter()
        self.cursor_to("읽어보세요.txt")

        self.app._view_file()

        self.assertEqual(type(self.app.modal).__name__, "ViewerDialog")


class TestExtracting(ArchivePaneTestCase):
    def run_copy(self):
        self.app._start_copy()
        self.assertIsNotNone(self.app.modal, "확인 창이 뜨지 않았습니다")
        self.app.modal._on_decide(True)
        self.app.modal = None
        # 워커가 끝날 때까지 기다린다
        for _ in range(200):
            if self.app.transfer is None:
                break
            self.app._drain_events()
            import time

            time.sleep(0.02)

    def test_a_file_comes_out(self):
        self.cursor_to("묶음.zip")
        self.app._enter()
        self.cursor_to("읽어보세요.txt")
        self.run_copy()

        self.assertEqual((self.there / "읽어보세요.txt").read_bytes(), b"top")

    def test_a_folder_comes_out_with_everything_inside(self):
        self.cursor_to("묶음.zip")
        self.app._enter()
        self.cursor_to("안쪽")
        self.run_copy()

        self.assertEqual((self.there / "안쪽" / "메모.txt").read_bytes(), b"inside")
        self.assertEqual(
            (self.there / "안쪽" / "더안쪽" / "깊은것.bin").read_bytes(), b"z" * 64
        )


class TestWhatIsRefused(ArchivePaneTestCase):
    """압축은 읽기 전용이다."""

    def test_you_cannot_copy_into_an_archive(self):
        self.app.right.navigate(self.here)
        self.app.right.enter_archive(self.box)
        self.app.focus = self.app.left
        self.cursor_to("묶음.zip")

        self.app._start_copy()

        self.assertIn("압축 안에는 넣을 수 없습니다", self.app.status)
        self.assertIsNone(self.app.modal)

    def test_you_cannot_move_out_of_an_archive(self):
        """옮기면 원본을 지워야 하는데 압축 안은 지울 수 없다."""
        self.cursor_to("묶음.zip")
        self.app._enter()
        self.cursor_to("읽어보세요.txt")

        self.app._start_move()

        self.assertIn("꺼내기(복사)만", self.app.status)
        self.assertIsNone(self.app.modal)


if __name__ == "__main__":
    unittest.main()
