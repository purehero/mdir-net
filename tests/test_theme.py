# MDir 옛 색(검은 바닥 16색)이 조용히 새지 않게 지키는 테스트
#
# 배경. 폭 안전(test_width_safety.py)과 같은 취지다. 색은 눈으로만 확인하기 쉬워서
# 한 번 정한 제약이 다음 수정에서 조용히 깨진다. 두 가지를 못 박는다.
#   - 확장색(256색.트루컬러)을 쓰지 않는다. 터미널마다 달라진다
#   - 화면을 떠날 때 배경색을 남기지 않는다. 셸이 검은 바닥으로 남는다

import re
import tempfile
import unittest
from pathlib import Path

from mdirnet.ui import app as app_module
from mdirnet.ui import screen, term

from .helpers import make_shares

_SGR = re.compile(r"\x1b\[([0-9;]*)m")

# 기본 16색만 쓴다. 밝은 색은 90-97(aixterm)이 아니라 BOLD + 30-37 로 낸다.
# 밝게 만드는 길이 하나여야 종류별 색과 커서 줄의 바탕색이 짝을 맞춘다
ALLOWED = {"", "0", "1", "2", "7", "22", "27"}
ALLOWED |= {str(n) for n in range(30, 38)}
ALLOWED |= {str(n) for n in range(40, 48)}


class ThemeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "폴더").mkdir()
        (self.root / "문서.txt").write_bytes(b"x")
        (self.root / "run.sh").write_bytes(b"#!/bin/sh\n")
        self.app = app_module.App(
            my_name="theme-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.refresh()


class TestOnlyTheBasic16Colors(ThemeTestCase):
    def test_composed_screen_uses_no_extended_colors(self):
        for line in self.app._compose((100, 30)):
            for params in _SGR.findall(line):
                for part in params.split(";"):
                    self.assertIn(
                        part,
                        ALLOWED,
                        "확장색이 섞였습니다: %r 안의 %r" % (params, part),
                    )

    def test_every_dialog_uses_no_extended_colors(self):
        # 대화상자에도 색이 있다. 화면 본체만 보면 놓친다
        for key in ("1", "n", "4", "7", "9"):
            self.app.modal = None
            self.app._on_key(key)
            self.assertIsNotNone(self.app.modal, key)
            for line in self.app._compose((100, 30)):
                for params in _SGR.findall(line):
                    for part in params.split(";"):
                        self.assertIn(part, ALLOWED, "%s: %r" % (key, params))

    def test_the_palette_constants_are_within_16_colors(self):
        for name in ("NORMAL", "RESET", "BOLD", "DIM", "BAR", "MARK", "GOOD", "BAD"):
            value = getattr(term, name)
            self.assertTrue(_SGR.fullmatch(value) or value == term.RESET, name)
            for params in _SGR.findall(value):
                for part in params.split(";"):
                    self.assertIn(part, ALLOWED, "%s = %r" % (name, value))


class TestFileColorsByType(ThemeTestCase):
    """MDir 은 파일 종류마다 글자색이 달랐다."""

    def _line_for(self, name):
        rows = self.app.local.rows()
        index = next(i for i, e in enumerate(rows) if e["name"] == name)
        # 커서가 얹히면 색이 바뀌므로 커서를 다른 줄에 둔다
        self.app.local.cursor = 0 if index != 0 else 1
        return self.app._entry_line(
            self.app.local, rows[index], index, 60, focused=True
        )

    def test_each_type_gets_its_own_color(self):
        for name, color in (
            ("run.sh", term.GREEN),
            ("모음.zip", term.RED),
            ("사진.jpg", term.MAGENTA),
            ("main.py", term.CYAN),
        ):
            (self.root / name).write_bytes(b"x")
        self.app.local.refresh()

        seen = {}
        for name, color in (
            ("run.sh", term.GREEN),
            ("모음.zip", term.RED),
            ("사진.jpg", term.MAGENTA),
            ("main.py", term.CYAN),
        ):
            line = self._line_for(name)
            self.assertTrue(line.startswith(term.ink(color)), "%s: %r" % (name, line[:20]))
            seen[name] = color
        self.assertEqual(len(set(seen.values())), 4, "종류별로 색이 갈려야 한다")

    def test_unknown_extension_stays_plain_white(self):
        (self.root / "메모.qqq").write_bytes(b"x")
        self.app.local.refresh()
        self.assertTrue(self._line_for("메모.qqq").startswith(term.ink(term.WHITE, False)))

    def test_directory_is_bright_white(self):
        self.assertTrue(self._line_for("폴더").startswith(term.ink(term.WHITE)))


class TestCursorLineInvertsTheEntryColor(ThemeTestCase):
    """커서 줄은 그 항목의 글자색을 바탕으로 깔고 글자를 검정으로 뒤집는다."""

    def _cursor_line(self, name):
        rows = self.app.local.rows()
        self.app.local.cursor = next(i for i, e in enumerate(rows) if e["name"] == name)
        return self.app._entry_line(
            self.app.local, rows[self.app.local.cursor], self.app.local.cursor, 60, focused=True
        )

    def test_background_is_the_entry_color_and_text_is_black(self):
        (self.root / "모음.zip").write_bytes(b"x")
        self.app.local.refresh()
        for name, color in (("모음.zip", term.RED), ("run.sh", term.GREEN), ("폴더", term.WHITE)):
            line = self._cursor_line(name)
            self.assertTrue(line.startswith(term.bar(color)), "%s: %r" % (name, line[:20]))
            self.assertIn("30;%d" % (color + 10), line[:12], name)

    def test_a_marked_file_keeps_its_yellow_under_the_cursor(self):
        self.app.local.selected.add("문서.txt")
        self.assertTrue(self._cursor_line("문서.txt").startswith(term.bar(term.YELLOW)))

    def test_bar_backgrounds_stay_in_the_basic_16(self):
        for color in (term.WHITE, term.RED, term.GREEN, term.CYAN, term.MAGENTA, term.YELLOW):
            for part in _SGR.fullmatch(term.bar(color)).group(1).split(";"):
                self.assertIn(part, ALLOWED, term.bar(color))


class TestTheBlackBackgroundSurvivesEveryLine(ThemeTestCase):
    """RESET 이 바탕색을 함께 들고 있어야 줄 끝까지 검다."""

    def test_reset_carries_the_theme_background(self):
        self.assertTrue(term.RESET.endswith(term.NORMAL), repr(term.RESET))

    def test_line_is_erased_to_the_end_with_the_theme_background(self):
        # \x1b[K 가 테마 배경이 켜진 상태에서 나가야 줄 오른쪽 끝까지 검다.
        # RESET 이 맨 배경 없는 \x1b[0m 으로 돌아가면 이 검사가 깨진다
        written = self._capture(lambda: screen.Renderer().render(["hello"], (10, 1)))
        self.assertIn(term.NORMAL + "\x1b[K", written)

    def test_full_repaint_paints_the_background_before_clearing(self):
        # 크기가 바뀌면 렌더러가 화면을 통째로 지운다. CLEAR 가 RESET 뒤에 와야
        # 지워진 자리가 검다. Terminal.__enter__ 도 같은 순서를 지키는데,
        # 그쪽은 실제 tty 가 필요해 여기서 부르지 못한다
        written = self._capture(lambda: screen.Renderer().render(["hi"], (5, 1)))
        self.assertLess(written.index(term.NORMAL), written.index(term.CLEAR))

    @staticmethod
    def _capture(action):
        out = []
        original = term.write
        term.write = out.append
        try:
            action()
        finally:
            term.write = original
        return "".join(out)


class TestLeavingTheScreenRestoresTheTerminal(unittest.TestCase):
    def test_restore_does_not_leave_the_background_color_behind(self):
        """종료 뒤 셸이 검은 바닥으로 남지 않아야 한다.

        restore() 가 그리는 데 쓰는 RESET 을 쓰면 그 증상이 난다. 나갈 때는
        배경까지 지우는 HARD_RESET 이다.
        """
        terminal = term.Terminal()
        terminal._active = True  # __enter__ 없이 복원 경로만 확인한다

        out = []
        original = term.write
        term.write = out.append
        try:
            terminal.restore()
        finally:
            term.write = original

        written = "".join(out)
        self.assertIn(term.HARD_RESET, written)
        self.assertNotIn(term.NORMAL, written)

    def test_hard_reset_is_a_bare_reset(self):
        self.assertEqual(term.HARD_RESET, "\x1b[0m")


if __name__ == "__main__":
    unittest.main()
