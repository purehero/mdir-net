# 마우스 - 시퀀스 해석과 좌표를 동작으로 되돌리기
#
# 마우스는 켜는 순간 **입력 경로 전체**를 지나간다. 시퀀스를 잘못 읽으면 그 바이트가
# 키로 새어들어와 엉뚱한 명령이 실행된다. 그래서 해석 쪽을 먼저, 꼼꼼히 본다.
#
# 그리고 끌 때 반드시 꺼야 한다. 켠 채로 나가면 셸에서 클릭할 때마다 이상한 글자가
# 찍힌다 - 사용자는 터미널이 망가진 줄 안다.

import tempfile
import unittest
from pathlib import Path

from mdirnet.ui import app as app_module
from mdirnet.ui import term

from .helpers import make_shares


class TestParsing(unittest.TestCase):
    """`ESC [ < 단추 ; 열 ; 행 M` 을 읽는다."""

    def test_a_left_click(self):
        event = term._mouse_from("0;12;5", pressed=True)

        self.assertEqual(event.kind, term.Mouse.PRESS)
        self.assertEqual((event.row, event.col), (5, 12))
        self.assertEqual(event.button, 0)

    def test_release(self):
        self.assertEqual(term._mouse_from("0;1;1", pressed=False).kind, term.Mouse.RELEASE)

    def test_the_wheel(self):
        self.assertEqual(term._mouse_from("64;1;1", True).kind, term.Mouse.WHEEL_UP)
        self.assertEqual(term._mouse_from("65;1;1", True).kind, term.Mouse.WHEEL_DOWN)

    def test_drag_is_dropped(self):
        """32 가 더해진 것은 끌기다. 쓰지 않으므로 조용히 버린다."""
        self.assertIsNone(term._mouse_from("32;5;5", True))
        self.assertIsNone(term._mouse_from("35;5;5", True))

    def test_rubbish_is_dropped_not_raised(self):
        """읽다 예외가 나면 UI 루프가 통째로 죽는다."""
        for body in ("", "x;y;z", "1;2", "1;2;3;4"):
            with self.subTest(body):
                self.assertIsNone(term._mouse_from(body, True))

    def test_big_coordinates_survive(self):
        """1006 확장을 쓰는 이유다. 옛 방식은 223칸에서 망가진다."""
        event = term._mouse_from("0;300;150", True)

        self.assertEqual((event.row, event.col), (150, 300))


class TestTurningItOnAndOff(unittest.TestCase):
    def test_windows_never_turns_it_on(self):
        """msvcrt 경로에는 이 시퀀스가 오지 않는다. 켜면 깨진 글자만 들어온다."""
        original = term.IS_WINDOWS
        term.IS_WINDOWS = True
        self.addCleanup(setattr, term, "IS_WINDOWS", original)

        terminal = term.Terminal()
        terminal.use_mouse(True)

        self.assertFalse(terminal._mouse)

    def test_it_is_off_after_restore(self):
        written = []
        original = term.write
        term.write = written.append
        self.addCleanup(setattr, term, "write", original)

        terminal = term.Terminal()
        terminal.use_mouse(True)
        self.assertTrue(terminal._mouse)

        terminal._active = True  # 화면에 들어간 척
        terminal.restore()

        self.assertFalse(terminal._mouse, "마우스를 켠 채로 나갔다")
        self.assertIn(term.MOUSE_OFF, written, "끄는 시퀀스를 안 보냈다")


class TestTheWholeSequence(unittest.TestCase):
    """진짜 바이트를 파서에 통과시킨다.

    `_mouse_from` 만 봐서는 CSI 갈래가 `<` 를 제대로 처리하는지 알 수 없다.
    거기서 새면 남은 바이트가 키로 들어와 엉뚱한 명령이 돌아간다.
    """

    def feed(self, raw):
        import os
        import sys

        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        os.write(write_fd, raw)
        os.close(write_fd)

        class FakeStdin:
            def fileno(self):
                return read_fd

        original = sys.stdin
        sys.stdin = FakeStdin()
        self.addCleanup(setattr, sys, "stdin", original)

        return term.Terminal()._read_key_posix(0.2)

    def test_a_click_comes_through_as_a_mouse_event(self):
        event = self.feed(b"\x1b[<0;12;5M")

        self.assertIsInstance(event, term.Mouse)
        self.assertEqual((event.kind, event.row, event.col), (term.Mouse.PRESS, 5, 12))

    def test_a_release_comes_through(self):
        self.assertEqual(self.feed(b"\x1b[<0;1;1m").kind, term.Mouse.RELEASE)

    def test_the_wheel_comes_through(self):
        self.assertEqual(self.feed(b"\x1b[<64;3;4M").kind, term.Mouse.WHEEL_UP)

    def test_ordinary_keys_still_work(self):
        """마우스 갈래를 더하면서 평범한 키를 망가뜨리지 않았는지."""
        self.assertEqual(self.feed(b"\x1b[A"), term.UP)
        self.assertEqual(self.feed(b"\x1b[5~"), term.PGUP)


class MouseAppTestCase(unittest.TestCase):
    COLS, ROWS = 100, 30

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for name in ("가.txt", "나.txt", "다.txt"):
            (self.root / name).write_bytes(b"x")
        (self.root / "폴더").mkdir()

        self.app = app_module.App(
            my_name="mouse-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.toggle_right_source()
        self.app.left.navigate(self.root)
        self.app.right.navigate(self.root)
        # 배치를 실제로 그려서 잡는다. 손으로 넣으면 그리는 쪽과 어긋난다
        self.app._compose((self.COLS, self.ROWS))

    def click(self, row, col, kind=None):
        self.app.handle_mouse(term.Mouse(kind or term.Mouse.PRESS, row, col))

    @property
    def first_row(self):
        """목록 첫 줄의 화면 행 (1부터). 머리글 1 + 제목 1 + 가로줄 1 다음."""
        return 4


class TestClicking(MouseAppTestCase):
    def test_clicking_a_row_moves_the_cursor(self):
        self.click(self.first_row + 2, 5)

        self.assertEqual(self.app.left.cursor, self.app.left.offset + 2)

    def test_clicking_the_other_pane_moves_the_focus(self):
        self.assertIs(self.app.focus, self.app.left)

        self.click(self.first_row, self.COLS - 5)

        self.assertIs(self.app.focus, self.app.right)

    def test_clicking_past_the_list_does_nothing(self):
        before = self.app.left.cursor

        self.click(self.first_row + 25, 5)  # 목록보다 아래

        self.assertEqual(self.app.left.cursor, before)

    def test_a_double_click_opens(self):
        rows = self.app.left.rows()
        index = next(i for i, r in enumerate(rows) if r["name"] == "폴더")
        screen_row = self.first_row + index - self.app.left.offset

        self.click(screen_row, 5)
        self.click(screen_row, 5)  # 곧바로 한 번 더

        self.assertEqual(self.app.left.path, self.root / "폴더")

    def test_two_slow_clicks_are_not_a_double_click(self):
        rows = self.app.left.rows()
        index = next(i for i, r in enumerate(rows) if r["name"] == "폴더")
        screen_row = self.first_row + index - self.app.left.offset

        self.click(screen_row, 5)
        self.app._last_click = (0.0, screen_row - self.first_row, 5)  # 오래 전이었다고 친다
        self.click(screen_row, 5)

        self.assertEqual(self.app.left.path, self.root)

    def test_clicks_at_different_spots_are_not_a_double_click(self):
        self.click(self.first_row, 5)
        self.click(self.first_row + 1, 5)

        self.assertEqual(self.app.left.path, self.root, "다른 자리인데 열렸다")


class TestWheel(MouseAppTestCase):
    def test_the_wheel_moves_the_cursor(self):
        self.app.left.cursor = 3
        self.click(self.first_row, 5, kind=term.Mouse.WHEEL_DOWN)

        self.assertGreater(self.app.left.cursor, 3)

    def test_the_wheel_works_on_the_pane_under_it_not_the_focused_one(self):
        """가리키는 곳이 움직여야 한다. 초점을 옮기지는 않는다."""
        self.app.right.cursor = 2
        self.click(self.first_row, self.COLS - 5, kind=term.Mouse.WHEEL_DOWN)

        self.assertGreater(self.app.right.cursor, 2)
        self.assertIs(self.app.focus, self.app.left, "휠이 초점을 옮겼다")


class TestFunctionBar(MouseAppTestCase):
    def test_clicking_the_bar_runs_that_key(self):
        self.click(self.ROWS, 2)  # 맨 아래 줄 왼쪽 끝 = 1도움

        self.assertEqual(type(self.app.modal).__name__, "MessageDialog")


class TestModalsIgnoreTheMouse(MouseAppTestCase):
    def test_a_click_does_nothing_while_a_dialog_is_open(self):
        self.app._show_help()
        before = self.app.left.cursor

        self.click(self.first_row + 2, 5)

        self.assertEqual(self.app.left.cursor, before)
        self.assertIsNotNone(self.app.modal, "클릭이 다이얼로그를 닫았다")


class TestTheToggle(MouseAppTestCase):
    def test_it_flips_and_says_so(self):
        before = self.app.use_mouse

        self.app.toggle_mouse()

        self.assertNotEqual(self.app.use_mouse, before)
        self.assertIn("마우스", self.app.status)


if __name__ == "__main__":
    unittest.main()
