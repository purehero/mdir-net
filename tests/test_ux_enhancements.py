# UI/UX 검토에서 제안된 개선들이 실제로 동작하는지 확인하는 테스트

import tempfile
import time
import unittest
from pathlib import Path

from mdirnet import fsops
from mdirnet.ui import app as app_module
from mdirnet.ui import screen, term

from .helpers import make_shares


class UxTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "폴더").mkdir()
        (self.root / "빈폴더").mkdir()
        (self.root / "메모.txt").write_bytes("한 줄\n두 줄\n".encode("utf-8"))

        self.app = app_module.App(
            my_name="ux",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.refresh()

    def cursor_on(self, name):
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == name
        )


class TestInputCursor(unittest.TestCase):
    """이름 바꾸기에서 앞부분만 고치려고 전부 지웠다 다시 치는 일이 없어야 한다."""

    def make(self, initial=""):
        self.got = []
        return app_module.InputDialog("t", "p", self.got.append, initial=initial)

    def test_cursor_starts_at_the_end(self):
        dialog = self.make("보고서.txt")
        self.assertEqual(dialog.cursor, len("보고서.txt"))

    def test_left_and_right_move(self):
        dialog = self.make("abc")
        dialog.key(term.LEFT)
        self.assertEqual(dialog.cursor, 2)
        dialog.key(term.RIGHT)
        self.assertEqual(dialog.cursor, 3)

    def test_cursor_clamps(self):
        dialog = self.make("ab")
        for _ in range(10):
            dialog.key(term.LEFT)
        self.assertEqual(dialog.cursor, 0)
        for _ in range(10):
            dialog.key(term.RIGHT)
        self.assertEqual(dialog.cursor, 2)

    def test_home_and_end(self):
        dialog = self.make("abcdef")
        dialog.key(term.HOME_KEY)
        self.assertEqual(dialog.cursor, 0)
        dialog.key(term.END_KEY)
        self.assertEqual(dialog.cursor, 6)

    def test_insert_in_the_middle(self):
        dialog = self.make("보고서.txt")
        dialog.key(term.HOME_KEY)
        for ch in "2026_":
            dialog.key(ch)
        self.assertEqual(dialog.text, "2026_보고서.txt")
        self.assertEqual(dialog.cursor, 5)

    def test_backspace_deletes_before_the_cursor(self):
        dialog = self.make("abcd")
        dialog.key(term.LEFT)
        dialog.key(term.BACKSPACE)
        self.assertEqual(dialog.text, "abd")
        self.assertEqual(dialog.cursor, 2)

    def test_delete_removes_at_the_cursor(self):
        dialog = self.make("abcd")
        dialog.key(term.HOME_KEY)
        dialog.key(term.DELETE)
        self.assertEqual(dialog.text, "bcd")
        self.assertEqual(dialog.cursor, 0)

    def test_backspace_at_the_start_does_nothing(self):
        dialog = self.make("ab")
        dialog.key(term.HOME_KEY)
        dialog.key(term.BACKSPACE)
        self.assertEqual(dialog.text, "ab")

    def test_hangul_insert_in_the_middle(self):
        dialog = self.make("보고서.txt")
        dialog.key(term.HOME_KEY)
        for ch in "최종":
            dialog.key(ch)
        self.assertEqual(dialog.text, "최종보고서.txt")

    def test_cursor_is_visible_in_the_rendering(self):
        dialog = self.make("abc")
        dialog.key(term.HOME_KEY)
        self.assertIn(term.REVERSE, dialog._rendered(40))

    def test_rendering_keeps_exact_width(self):
        for text in ("", "abc", "한글이름.txt", "긴" * 40):
            dialog = self.make(text)
            self.assertEqual(screen.width(dialog._rendered(40)), 40, text)

    def test_footer_mentions_the_movement_keys(self):
        dialog = self.make("x")
        self.assertIn("Home", "\n".join(dialog.lines(60)))


class TestEnterOnFile(UxTestCase):
    def test_enter_on_a_file_opens_the_viewer(self):
        self.cursor_on("메모.txt")
        self.app._on_key(term.ENTER)
        self.assertIsInstance(self.app.modal, app_module.ViewerDialog)

    def test_enter_on_a_directory_still_navigates(self):
        self.cursor_on("폴더")
        self.app._on_key(term.ENTER)
        self.assertEqual(self.app.local.path, self.root / "폴더")

    def test_enter_on_dotdot_still_goes_up(self):
        self.app.local.navigate(self.root / "폴더")
        self.app.local.cursor = 0
        self.app._on_key(term.ENTER)
        self.assertEqual(self.app.local.path, self.root)


class TestBackspaceGoesUp(UxTestCase):
    def test_backspace_is_like_left_arrow(self):
        """맥 노트북의 delete 키는 Backspace 로 들어온다."""
        self.app.local.navigate(self.root / "폴더")
        self.app._on_key(term.BACKSPACE)
        self.assertEqual(self.app.local.path, self.root)

    def test_backspace_still_edits_inside_an_input_box(self):
        self.app._on_key("7")
        for ch in "abc":
            self.app._on_key(ch)
        self.app._on_key(term.BACKSPACE)
        self.assertEqual(self.app.modal.text, "ab")


class TestPageSizeFollowsHeight(UxTestCase):
    def test_page_moves_by_the_visible_height(self):
        for index in range(40):
            (self.root / ("f%02d.txt" % index)).write_bytes(b"x")
        self.app.local.refresh()

        self.app._compose((100, 30))  # 높이를 앱에 알린다
        height = self.app._body_height

        self.app.local.cursor = 0
        self.app._on_key(term.PGDN)
        self.assertEqual(self.app.local.cursor, height)

    def test_page_size_differs_between_window_heights(self):
        for index in range(60):
            (self.root / ("g%02d.txt" % index)).write_bytes(b"x")
        self.app.local.refresh()

        moved = []
        for rows in (14, 40):
            self.app._compose((100, rows))
            self.app.local.cursor = 0
            self.app._on_key(term.PGDN)
            moved.append(self.app.local.cursor)
        self.assertNotEqual(moved[0], moved[1], moved)


class TestEmptyFolderMarker(UxTestCase):
    def test_empty_folder_says_so(self):
        self.app.local.navigate(self.root / "빈폴더")
        self.assertIn("(빈 폴더)", "\n".join(self.app._compose((100, 24))))

    def test_non_empty_folder_has_no_marker(self):
        self.assertNotIn("(빈 폴더)", "\n".join(self.app._compose((100, 24))))

    def test_disconnected_remote_pane_has_no_marker(self):
        """연결 전 오른쪽은 '빈 폴더' 가 아니라 연결 안내가 맞다."""
        body = "\n".join(self.app._compose((100, 24)))
        self.assertEqual(body.count("(빈 폴더)"), 0)


class TestSortLabelInFooter(UxTestCase):
    def test_footer_shows_the_current_sort(self):
        self.assertIn("이름순", self.app._pane_summary(self.app.local))

    def test_footer_follows_a_sort_change(self):
        self.app._on_key("2")
        self.assertIn("확장자순", self.app._pane_summary(self.app.local))

    def test_each_pane_shows_its_own(self):
        self.app._on_key("=")  # 오른쪽을 내 PC 로
        self.app.set_sort(self.app.right, fsops.SORT_SIZE)
        self.assertIn("이름순", self.app._pane_summary(self.app.left))
        self.assertIn("크기순", self.app._pane_summary(self.app.right))


class TestTransferRate(unittest.TestCase):
    def test_no_speed_before_any_data(self):
        t = app_module.Transfer("x", 1000)
        self.assertIn("재는 중", t.rate_text())

    def test_speed_and_eta_after_progress(self):
        t = app_module.Transfer("x", 10_000_000)
        t.started = time.monotonic() - 2.0
        t.done = 2_000_000

        text = t.rate_text()
        self.assertIn("/s", text)
        self.assertIn("남음", text)

    def test_eta_uses_minutes_for_long_transfers(self):
        t = app_module.Transfer("x", 600_000_000)
        t.started = time.monotonic() - 2.0
        t.done = 2_000_000
        self.assertIn("분 남음", t.rate_text())

    def test_no_eta_when_finished(self):
        t = app_module.Transfer("x", 1000)
        t.started = time.monotonic() - 2.0
        t.done = 1000
        self.assertNotIn("남음", t.rate_text())

    def test_progress_dialog_shows_the_rate(self):
        t = app_module.Transfer("2개 항목", 10_000_000)
        t.started = time.monotonic() - 2.0
        t.done = 2_000_000
        rendered = "\n".join(app_module.ProgressDialog(t).lines(60))
        self.assertIn("/s", rendered)

    def test_rate_text_is_ascii_safe(self):
        import unicodedata

        t = app_module.Transfer("x", 10_000_000)
        t.started = time.monotonic() - 2.0
        t.done = 2_000_000
        for ch in t.rate_text():
            self.assertNotEqual(unicodedata.east_asian_width(ch), "A", repr(ch))


class TestQuitConfirmation(UxTestCase):
    def test_quits_immediately_when_idle(self):
        self.app._on_key("q")
        self.assertFalse(self.app.running)

    def test_asks_when_connected(self):
        self.app.session = _Stub()
        self.app._on_key("q")
        self.assertTrue(self.app.running)
        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)

    def test_confirming_quits(self):
        self.app.session = _Stub()
        self.app._on_key("q")
        self.app._on_key("y")
        self.assertFalse(self.app.running)

    def test_declining_keeps_running(self):
        self.app.session = _Stub()
        self.app._on_key("q")
        self.app._on_key("n")
        self.assertTrue(self.app.running)

    def test_asks_during_a_transfer(self):
        self.app.transfer = app_module.Transfer("x", 100)
        self.app._on_key("0")
        self.assertTrue(self.app.running)
        self.assertIn("전송", "\n".join(self.app.modal.lines(60)))

    def test_ctrl_c_cancels_a_transfer_before_quitting(self):
        self.app.transfer = app_module.Transfer("x", 100)
        self.app._on_key("CTRL-C")
        self.assertTrue(self.app.running)
        self.assertTrue(self.app.transfer.cancelled)


class TestOverwriteWarning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.src = self.root / "원본"
        self.dst = self.root / "대상"
        self.src.mkdir()
        self.dst.mkdir()
        (self.src / "같은이름.txt").write_bytes(b"new")
        (self.src / "새파일.txt").write_bytes(b"new")
        (self.dst / "같은이름.txt").write_bytes(b"old")

        self.app = app_module.App(
            my_name="o",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app._on_key("=")
        self.app.right.navigate(self.dst)
        self.app.left.navigate(self.src)

    def confirm_body(self, selected):
        self.app.modal = None
        self.app.left.selected = set(selected)
        self.app._on_key("5")
        return "\n".join(self.app.modal.lines(60))

    def test_no_warning_without_a_clash(self):
        self.assertNotIn("덮어씁니다", self.confirm_body({"새파일.txt"}))

    def test_warns_with_the_clashing_names(self):
        body = self.confirm_body({"같은이름.txt", "새파일.txt"})
        self.assertIn("덮어씁니다", body)
        self.assertIn("같은이름.txt", body)
        self.assertIn("기존 1개", body)

    def test_counts_multiple_clashes(self):
        (self.src / "또같은.txt").write_bytes(b"n")
        (self.dst / "또같은.txt").write_bytes(b"o")
        self.app.left.refresh()
        self.assertIn("기존 2개", self.confirm_body({"같은이름.txt", "또같은.txt"}))


class TestPeerNotice(UxTestCase):
    class FakeDiscovery:
        def __init__(self, names):
            self.names = names

        def peers(self):
            return [type("P", (), {"name": n})() for n in self.names]

        def stop(self):
            pass

    def test_new_peer_is_announced(self):
        self.app.discovery = self.FakeDiscovery(["office-pc"])
        self.app._notice_new_peers()
        self.assertIn("office-pc", self.app.status)
        self.assertIn("n", self.app.status)

    def test_same_peer_is_not_announced_twice(self):
        self.app.discovery = self.FakeDiscovery(["office-pc"])
        self.app._notice_new_peers()
        self.app.status = "무언가 다른 안내"
        self.app._notice_new_peers()
        self.assertEqual(self.app.status, "무언가 다른 안내")

    def test_a_second_peer_is_announced(self):
        self.app.discovery = self.FakeDiscovery(["a-pc"])
        self.app._notice_new_peers()
        self.app.discovery = self.FakeDiscovery(["a-pc", "b-pc"])
        self.app._notice_new_peers()
        self.assertIn("b-pc", self.app.status)
        self.assertNotIn("a-pc", self.app.status)

    def test_quiet_while_connected(self):
        self.app.session = _Stub()
        self.app.discovery = self.FakeDiscovery(["office-pc"])
        self.app.status = "그대로"
        self.app._notice_new_peers()
        self.assertEqual(self.app.status, "그대로")

    def test_quiet_while_a_dialog_is_open(self):
        self.app.discovery = self.FakeDiscovery(["office-pc"])
        self.app._on_key("1")  # 도움말
        self.app.status = "그대로"
        self.app._notice_new_peers()
        self.assertEqual(self.app.status, "그대로")


class TestLoadingTextIsReadable(UxTestCase):
    def test_no_tilde_ellipsis(self):
        """'불러오는 중~' 은 물결 애교처럼 읽힌다. ASCII 마침표 세 개를 쓴다."""
        import pathlib as _pathlib

        source = _pathlib.Path(app_module.__file__).read_text()
        self.assertNotIn("중~", source)
        self.assertIn("불러오는 중...", source)


class _Stub:
    peer_name = "stub-pc"
    peer_host = "127.0.0.1"

    def close(self, reason=None):
        pass


if __name__ == "__main__":
    unittest.main()
