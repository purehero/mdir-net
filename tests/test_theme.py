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

from mdirnet import settings
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


# MDir 3.06 BLACK.COL(초기치)에서 읽어낸 갈래별 색. 원본 확장자 하나씩 골랐다.
# 이 표가 깨지면 옛 화면과 색이 달라진 것이다
MDIR_SAMPLES = (
    ("prog.exe", term.GREEN, True, "EXE 밝은초록"),
    ("prog.com", term.CYAN, True, "COM 하늘색"),
    ("go.bat", term.YELLOW, True, "BAT 노랑"),
    ("모음.lzh", term.MAGENTA, True, "압축 밝은자홍"),
    ("문서.txt", term.CYAN, False, "문서 청록"),
    ("옛것.bak", term.RED, False, "백업 빨강"),
    ("main.pas", term.BLUE, True, "소스 밝은파랑"),
    ("main.h", term.BLUE, False, "헤더 파랑"),
    ("사진.gif", term.GREEN, False, "그림 초록"),
    ("소리.voc", term.YELLOW, False, "소리 갈색"),
)


class TestFileColorsByType(ThemeTestCase):
    """MDir 은 파일 종류마다 글자색이 달랐다. 값은 BLACK.COL 에서 뽑았다."""

    def _line_for(self, name):
        rows = self.app.local.rows()
        index = next(i for i, e in enumerate(rows) if e["name"] == name)
        # 커서가 얹히면 색이 바뀌므로 커서를 다른 줄에 둔다
        self.app.local.cursor = 0 if index != 0 else 1
        return self.app._entry_line(
            self.app.local, rows[index], index, 60, focused=True
        )

    def test_every_mdir_group_keeps_its_original_color(self):
        for name, _, _, _ in MDIR_SAMPLES:
            (self.root / name).write_bytes(b"x")
        self.app.local.refresh()

        for name, color, bright, label in MDIR_SAMPLES:
            line = self._line_for(name)
            self.assertTrue(
                line.startswith(term.ink(color, bright)),
                "%s (%s): %r" % (name, label, line[:20]),
            )

    def test_the_groups_are_visually_distinct(self):
        """열 갈래가 서로 다른 코드여야 한다. 겹치면 종류를 못 가린다."""
        codes = {term.ink(c, b) for _, c, b, _ in MDIR_SAMPLES}
        self.assertEqual(len(codes), len(MDIR_SAMPLES), codes)

    def test_no_extension_belongs_to_two_groups(self):
        seen = {}
        for extensions, color, bright in app_module.FILE_COLORS:
            for ext in extensions:
                self.assertNotIn(ext, seen, "%s 가 두 갈래에 있다" % ext)
                seen[ext] = color

    def test_unknown_extension_stays_plain_white(self):
        (self.root / "메모.qqq").write_bytes(b"x")
        self.app.local.refresh()
        self.assertTrue(self._line_for("메모.qqq").startswith(term.ink(term.WHITE, False)))

    def test_directory_is_bright_white(self):
        self.assertTrue(self._line_for("폴더").startswith(term.ink(term.WHITE)))


class TestCursorLineInvertsTheEntryColor(ThemeTestCase):
    """커서 줄은 그 항목의 글자색을 바탕으로 깔고 글자를 뒤집는다."""

    def _cursor_line(self, name):
        rows = self.app.local.rows()
        self.app.local.cursor = next(i for i, e in enumerate(rows) if e["name"] == name)
        return self.app._entry_line(
            self.app.local, rows[self.app.local.cursor], self.app.local.cursor, 60, focused=True
        )

    def test_background_is_the_entry_color(self):
        (self.root / "모음.lzh").write_bytes(b"x")
        (self.root / "문서.txt").write_bytes(b"x")
        self.app.local.refresh()
        for name, color, bright in (
            ("모음.lzh", term.MAGENTA, True),
            ("run.sh", term.YELLOW, True),
            ("문서.txt", term.CYAN, False),
            ("폴더", term.WHITE, True),
        ):
            line = self._cursor_line(name)
            self.assertTrue(
                line.startswith(term.bar(color, bright)), "%s: %r" % (name, line[:20])
            )
            self.assertIn(";%d" % (color + 10), line[:12], name)

    def test_dark_backgrounds_get_white_text(self):
        """바탕이 어두우면 검은 글자로는 안 읽힌다.

        밝기는 배경에 실을 수 없으므로, 글자색이 밝든 어둡든 바탕은 어두운
        색으로 깔린다. 밝은자홍(압축)의 바탕은 자홍이고 밝은파랑(소스)의 바탕은
        파랑이다. 그래서 밝은 갈래도 흰 글자를 받는다.
        """
        # 검정은 빼둔다. 검은 바닥에서는 _visible() 이 밝은회색으로 바꿔치우므로
        # 바탕이 밝아진다. 그 경우는 test_the_bar_is_never_invisible 이 지킨다
        colors = (term.CYAN, term.RED, term.GREEN, term.BLUE, term.YELLOW, term.MAGENTA)
        for color in colors:
            for bright in (True, False):
                code = term.bar(color, bright)
                self.assertTrue(
                    code.startswith("\x1b[%d;" % term.WHITE),
                    "%s bright=%s -> %r" % (color, bright, code),
                )

    def test_light_gray_is_the_only_one_with_black_text(self):
        """밝은회색만 바탕이 밝게 깔린다. BLACK.COL 의 70 이 이 값이다."""
        self.assertEqual(term.bar(term.WHITE, False), "\x1b[30;47m")
        self.assertEqual(term.bar(term.WHITE, True), "\x1b[30;47m")

    def test_the_bar_is_never_invisible(self):
        """바탕색과 글자색이 같아지는 조합이 없어야 한다."""
        for color in (term.WHITE, term.RED, term.GREEN, term.YELLOW, term.BLUE,
                      term.MAGENTA, term.CYAN, term.BLACK):
            for bright in (True, False):
                text, background = _SGR.fullmatch(term.bar(color, bright)).group(1).split(";")
                self.assertNotEqual(
                    int(text) + 10, int(background), (color, bright, term.bar(color, bright))
                )

    def test_bright_and_dark_share_one_bar_background(self):
        """밝기는 배경에 실을 수 없다. VGA 에서 그 자리는 깜빡임이었다."""
        self.assertEqual(
            term.bar(term.CYAN, True).split(";")[-1],
            term.bar(term.CYAN, False).split(";")[-1],
        )

    def test_a_marked_file_keeps_its_yellow_under_the_cursor(self):
        self.app.local.selected.add("문서.txt")
        self.assertTrue(
            self._cursor_line("문서.txt").startswith(term.bar(term.YELLOW, True))
        )

    def test_bar_backgrounds_stay_in_the_basic_16(self):
        for color in (term.WHITE, term.RED, term.GREEN, term.CYAN, term.MAGENTA, term.YELLOW):
            for bright in (True, False):
                code = term.bar(color, bright)
                for part in _SGR.fullmatch(code).group(1).split(";"):
                    self.assertIn(part, ALLOWED, code)


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


class TestSwitchingColorSets(ThemeTestCase):
    """MDir 배포판에 든 색상셋 둘을 갈아끼운다."""

    def tearDown(self):
        term.use_theme("black")  # 다른 테스트에 색을 물려주지 않는다

    def test_there_are_exactly_the_two_sets_mdir_shipped(self):
        self.assertEqual(term.THEMES, ("black", "blue"))

    def test_black_is_the_default(self):
        self.assertEqual(term.theme(), "black")
        self.assertEqual(term.NORMAL, "\x1b[37;40m")

    def test_blue_changes_only_the_background(self):
        before = {name: getattr(term, name) for name in ("NORMAL", "DIM", "MARK", "GOOD", "BAD")}
        term.use_theme("blue")
        for name, old in before.items():
            new = getattr(term, name)
            self.assertNotEqual(new, old, name)
            # 글자색 쪽은 그대로고 배경만 40 -> 44 로 바뀐다
            self.assertEqual(new.replace(";44m", ";40m"), old, name)

    def test_reset_follows_the_new_background(self):
        term.use_theme("blue")
        self.assertTrue(term.RESET.endswith(term.NORMAL))
        self.assertIn(";44m", term.RESET)

    def test_a_color_that_matches_the_background_is_swapped_out(self):
        """파란 바닥에서 파랑 글자는 안 보인다. BLUE.COL 도 검정으로 바꿔놓았다."""
        term.use_theme("blue")
        self.assertEqual(term.ink(term.BLUE, False), "\x1b[30;44m")
        # 그 항목의 커서 줄은 검은 바탕이 되고, 어두우므로 흰 글자를 얹는다
        self.assertEqual(term.bar(term.BLUE, False), "\x1b[37;40m")

    def test_the_bright_variant_of_the_background_color_survives(self):
        """밝은파랑(9)과 파랑(1)은 DOS 에서 다른 색이다. 소스 색이 살아야 한다.

        BLUE.COL 은 소스(PAS C CPP ASM BAS CBL)를 밝은파랑 그대로 두고
        H OBJ LIB 만 검정으로 바꿨다. 배경은 어두운 8색뿐이라 밝은 것은 묻히지
        않기 때문이다.
        """
        term.use_theme("blue")
        self.assertEqual(term.ink(term.BLUE, True), "\x1b[1;34;44m")

    def test_source_and_header_stay_apart_in_both_sets(self):
        for name in term.THEMES:
            term.use_theme(name)
            self.assertNotEqual(
                term.ink(term.BLUE, True), term.ink(term.BLUE, False), name
            )

    def test_unknown_set_is_refused(self):
        with self.assertRaises(ValueError):
            term.use_theme("green")
        self.assertEqual(term.theme(), "black", "실패한 뒤에도 이전 색을 지킨다")

    def test_toggle_switches_and_forces_a_full_repaint(self):
        original = term.write
        term.write = lambda text: None  # 테스트 출력에 이스케이프가 섞이지 않게
        try:
            self.app.renderer.render(["첫 프레임"], (20, 1))
        finally:
            term.write = original
        self.assertNotEqual(self.app.renderer._prev, [], "그린 프레임이 남아있어야 한다")

        self.app.toggle_theme()
        self.assertEqual(term.theme(), "blue")
        self.assertEqual(self.app.renderer._prev, [], "통째로 다시 그려야 한다")
        self.app.toggle_theme()
        self.assertEqual(term.theme(), "black")

    def test_toggle_does_not_write_when_persist_is_off(self):
        """persist=False 는 저장 파일을 건드리지 않는다는 뜻이다."""
        import mdirnet.settings as settings_mod

        calls = []
        original = settings_mod.save_theme
        settings_mod.save_theme = lambda name: calls.append(name)
        try:
            self.assertFalse(self.app.persist)
            self.app.toggle_theme()
        finally:
            settings_mod.save_theme = original
        self.assertEqual(calls, [])

    def test_every_set_keeps_the_screen_inside_the_basic_16(self):
        for name in term.THEMES:
            term.use_theme(name)
            for line in self.app._compose((100, 30)):
                for params in _SGR.findall(line):
                    for part in params.split(";"):
                        self.assertIn(part, ALLOWED, "%s: %r" % (name, params))


class TestSavingTheChoice(unittest.TestCase):
    """고른 색상셋은 다음 실행에도 남아야 한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 사용자의 진짜 ~/.mdirnet 을 건드리지 않도록 저장 위치를 옮긴다
        fake = Path(self.tmp.name) / ".mdirnet" / "shares.json"
        original = settings.default_config_path
        settings.default_config_path = lambda: fake
        self.addCleanup(lambda: setattr(settings, "default_config_path", original))

    def test_round_trip(self):
        settings.save_theme("blue")
        self.assertEqual(settings.load_theme(), "blue")
        settings.save_theme("black")
        self.assertEqual(settings.load_theme(), "black")

    def test_missing_file_falls_back_to_the_default(self):
        self.assertEqual(settings.load_theme(), "black")

    def test_broken_file_falls_back_instead_of_crashing(self):
        settings.save_theme("blue")
        settings.default_config_path().with_name("settings.json").write_text("{ 망가진")
        self.assertEqual(settings.load_theme(), "black")

    def test_it_lands_beside_the_share_list(self):
        settings.save_theme("blue")
        saved = settings.default_config_path().with_name("settings.json")
        self.assertTrue(saved.exists())
        self.assertEqual(saved.parent, settings.default_config_path().parent)
        # 임시 파일을 남기지 않는다
        self.assertEqual(list(saved.parent.glob("*.tmp")), [])


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
