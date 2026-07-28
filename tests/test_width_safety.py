# 문자 폭 계산이 어긋나 화면이 밀리는 문제를 막는 테스트
#
# 배경. East Asian Width 가 A(Ambiguous)인 문자는 한글·CJK 환경 터미널에서
# 두 칸으로 그려진다. 이를 한 칸으로 세면 줄이 계산보다 길어져 테두리가 밀리거나
# 줄이 넘쳐 화면이 깨진다. 실제로 세 가지 증상이 이 한 원인에서 나왔다.
#   - '…' 생략 표시 → 줄 전체가 1칸 밀림
#   - 헤더의 '·' 두 개 → 포트번호 2자리가 잘림
#   - 박스 문자 → Windows 콘솔에서 네모칸이 닫히지 않음

import pathlib
import tempfile
import unicodedata
import unittest
from pathlib import Path

from mdirnet.ui import app as app_module
from mdirnet.ui import screen

from .helpers import make_shares, only_name, rpath




SOURCE_DIR = pathlib.Path(__file__).resolve().parent.parent / "mdirnet"


class TestNoAmbiguousWidthInSource(unittest.TestCase):
    """우리가 그리는 장식 문자에 Ambiguous 폭 문자를 쓰지 않는다.

    이 테스트가 이번 버그의 재발을 막는 핵심이다. 새 장식 문자를 넣을 때
    폭이 애매한 것을 골랐다면 여기서 걸린다.
    """

    def test_no_ambiguous_width_characters(self):
        offenders = {}
        for path in sorted(SOURCE_DIR.rglob("*.py")):
            for line_no, line in enumerate(path.read_text().splitlines(), 1):
                for ch in line:
                    if ord(ch) > 127 and unicodedata.east_asian_width(ch) == "A":
                        offenders.setdefault(ch, []).append(
                            "%s:%d" % (path.relative_to(SOURCE_DIR.parent), line_no)
                        )

        self.assertEqual(
            offenders,
            {},
            "Ambiguous 폭 문자를 발견했습니다. ASCII 로 바꾸세요.\n"
            + "\n".join(
                "  %r (U+%04X %s) → %s"
                % (ch, ord(ch), unicodedata.name(ch, "?"), ", ".join(spots[:4]))
                for ch, spots in offenders.items()
            ),
        )

    def test_drawing_constants_are_width_safe(self):
        """테두리 문자의 조건은 'ASCII' 가 아니라 '폭이 명확히 1칸' 이다.

        모서리 문자(U+231C~231F)는 ASCII 가 아니지만 EAW 가 N 이어서 안전하다.
        반대로 박스 문자(U+2500~)는 보기엔 딱 맞지만 Ambiguous 라 못 쓴다.
        """
        for name in ("BOX_H", "BOX_V", "BOX_TL", "BOX_TR", "BOX_BL", "BOX_BR"):
            value = getattr(app_module, name)
            self.assertEqual(len(value), 1, "%s = %r" % (name, value))
            self.assertEqual(screen.width(value), 1, "%s = %r" % (name, value))
            self.assertNotEqual(
                unicodedata.east_asian_width(value),
                "A",
                "%s = %r 은 Ambiguous 폭입니다" % (name, value),
            )


class TestCharWidthErrsSafely(unittest.TestCase):
    def test_ambiguous_counts_as_two(self):
        """넘치는 쪽이 모자란 쪽보다 나쁘므로 2로 센다."""
        for ch in "·…→←↑↓●○─│":
            self.assertEqual(screen.char_width(ch), 2, ch)

    def test_wide_still_two(self):
        for ch in "한글漢字あ":
            self.assertEqual(screen.char_width(ch), 2, ch)

    def test_ascii_stays_one(self):
        for ch in "aZ0-+|.~*<>^v_#":
            self.assertEqual(screen.char_width(ch), 1, ch)

    def test_combining_marks_take_no_space(self):
        self.assertEqual(screen.char_width("́"), 0)


class LayoutTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.app = app_module.App(
            my_name="width-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)

    def assert_exact(self, sizes=((80, 24), (100, 30), (120, 40), (40, 12), (200, 60))):
        for size in sizes:
            for index, line in enumerate(self.app._compose(size)):
                self.assertEqual(
                    screen.width(line),
                    size[0],
                    "size=%s line %d: %r" % (size, index, line),
                )


class TestFilenamesWithTrickyCharacters(LayoutTestCase):
    """사용자가 보고한 증상 1 — 파일명 때문에 칸이 밀리는 문제."""

    def test_filename_with_three_dots(self):
        (self.root / "보고서...txt").write_bytes(b"x")
        (self.root / "a...b...c.bin").write_bytes(b"y")
        self.app.local.refresh()
        self.assert_exact()

    def test_filename_with_the_ellipsis_character(self):
        """파일명 자체에 '…' 가 들어있어도 테두리가 유지되어야 한다."""
        (self.root / "회의록….txt").write_bytes(b"x")
        self.app.local.refresh()
        self.assert_exact()

    def test_filename_with_middle_dots_and_arrows(self):
        (self.root / "A·B·C.txt").write_bytes(b"x")
        (self.root / "before→after.log").write_bytes(b"y")
        self.app.local.refresh()
        self.assert_exact()

    def test_very_long_names_that_must_be_truncated(self):
        for name in (
            "아주아주아주아주아주긴한글파일이름입니다정말로깁니다.bin",
            "a" * 200 + ".txt",
            "한글과English가섞인아주긴이름" * 5 + ".dat",
        ):
            (self.root / name).write_bytes(b"z")
        self.app.local.refresh()
        self.assert_exact()

    def test_truncated_name_never_exceeds_its_column(self):
        (self.root / ("긴" * 100 + ".txt")).write_bytes(b"z")
        self.app.local.refresh()
        for size in ((80, 24), (40, 12)):
            for line in self.app._compose(size):
                self.assertLessEqual(screen.width(line), size[0])


class TestHeaderPortNumber(LayoutTestCase):
    """사용자가 보고한 증상 3 — 포트번호 2자리가 잘리던 문제.

    원인은 헤더에 있던 '·' 두 개였다. 각각 1칸으로 세고 2칸으로 그려져
    정확히 2칸이 밀려나갔다.
    """

    def test_full_port_number_is_visible_at_normal_widths(self):
        self.app.port = 52719
        for cols in (80, 90, 100, 120, 160):
            header = self.app._header(cols)
            self.assertIn("52719", header, "cols=%d: %r" % (cols, header))
            self.assertEqual(screen.width(header), cols, cols)

    def test_five_digit_port_survives_the_narrowest_width_that_shows_it(self):
        self.app.port = 65535
        header = self.app._header(80)
        self.assertIn("65535", header)

    def test_header_is_exact_width_for_every_size(self):
        self.app.port = 52719
        for cols in range(40, 201, 7):
            self.assertEqual(screen.width(self.app._header(cols)), cols, cols)

    def test_narrow_header_drops_to_a_shorter_form_instead_of_cutting(self):
        """자리가 없으면 잘라내지 말고 짧은 표현으로 물러나야 한다."""
        self.app.port = 52719
        header = self.app._header(44)
        self.assertEqual(screen.width(header), 44)
        # 잘린 숫자가 남아있지 않아야 한다 (527, 5271 같은 조각)
        self.assertNotIn("5271", header.replace("52719", ""))

    def test_connected_header_shows_peer_and_keeps_width(self):
        self.app.session = _StubSession()
        for cols in (50, 80, 120):
            self.assertEqual(screen.width(self.app._header(cols)), cols, cols)


class TestPaneBoxIsClosed(LayoutTestCase):
    """사용자가 보고한 증상 2 — Windows 에서 네모칸이 닫히지 않던 문제."""

    def test_every_pane_row_starts_and_ends_with_a_border(self):
        (self.root / "폴더").mkdir()
        (self.root / "한글 이름.txt").write_bytes(b"x")
        self.app.local.refresh()

        cols, rows = 100, 30
        lines = self.app._compose((cols, rows))
        pane_lines = lines[1:-2]  # 머리글과 상태줄·기능키 바를 제외한 패널 영역

        last = len(pane_lines) - 1
        for index, line in enumerate(pane_lines):
            plain = screen._ANSI.sub("", line)
            # 첫 줄은 위 모서리, 마지막 줄은 아래 모서리, 사이는 세로 테두리
            if index == 0:
                expected = app_module.BOX_TL
            elif index == last:
                expected = app_module.BOX_BL
            else:
                expected = app_module.BOX_V
            self.assertEqual(plain[0], expected, "왼쪽 테두리 없음: %r" % plain[:10])
            self.assertEqual(screen.width(line), cols)

    def test_box_corners_appear_on_the_right_edge(self):
        lines = self.app._compose((100, 30))
        top = screen._ANSI.sub("", lines[1])
        self.assertTrue(top.endswith(app_module.BOX_TR), "오른쪽 위 모서리 없음: %r" % top[-8:])

    def test_no_glyph_on_screen_has_ambiguous_width(self):
        (self.root / "파일.txt").write_bytes(b"x")
        self.app.local.refresh()
        for line in self.app._compose((100, 30)):
            frame = "".join(ch for ch in screen._ANSI.sub("", line) if ord(ch) > 127)
            for ch in frame:
                # 화면에 나오는 모든 비ASCII 문자는 폭이 명확해야 한다.
                # 파일 이름, 한글 라벨, 모서리 문자가 여기 해당한다
                self.assertIn(
                    unicodedata.east_asian_width(ch), ("W", "F", "N", "Na", "H"), repr(ch)
                )


class TestDialogsKeepWidth(LayoutTestCase):
    def test_every_dialog_keeps_exact_line_widths(self):
        (self.root / "메모.txt").write_bytes("한 줄\n두 줄\n".encode("utf-8"))
        (self.root / "blob.bin").write_bytes(bytes(range(256)))
        self.app.local.refresh()

        openers = [("1", "도움말"), ("2", "연결"), ("4", "공유폴더"), ("7", "만들기")]
        for key, label in openers:
            self.app.modal = None
            self.app._on_key(key)
            self.assertIsNotNone(self.app.modal, label)
            self.assert_exact(sizes=((80, 24), (100, 30), (120, 40)))

    def test_viewer_and_progress_keep_width(self):
        (self.root / "blob.bin").write_bytes(bytes(range(256)) * 3)
        self.app.local.refresh()
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == "blob.bin"
        )
        self.app._on_key("3")
        self.assert_exact(sizes=((80, 24), (120, 40)))

        transfer = app_module.Transfer("2개 항목", 1000)
        transfer.done = 333
        transfer.current = "sub/한글파일.bin"
        self.app.modal = app_module.ProgressDialog(transfer)
        self.assert_exact(sizes=((80, 24), (120, 40)))


class _StubSession:
    peer_name = "상대PC"
    peer_host = "127.0.0.1"

    def close(self, reason=None):
        pass


if __name__ == "__main__":
    unittest.main()
