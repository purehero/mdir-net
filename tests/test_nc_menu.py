# NC 식 상단 메뉴와 MDir 식 파일 목록 테스트
#
# 참고 화면. 위는 Norton Commander (Left | Files | Commands | Options | Right,
# F9 로 메뉴, 하단에 9Menu), 아래는 MDir III (이름/확장자/[SubDir]|크기/날짜/시간
# 칸과 `19 File 6 Dir 1,901,416 Byte` 요약).

import tempfile
import time
import unittest
from pathlib import Path

from mdirnet import fsops
from mdirnet.ui import app as app_module
from mdirnet.ui import screen, term

from .helpers import make_shares


class MdirListTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

        (self.root / "FONT").mkdir()
        (self.root / "한글폴더").mkdir()
        (self.root / "MERGEDIC.EXE").write_bytes(b"x" * 15210)
        (self.root / "HWP.CFG").write_bytes(b"x" * 1054)
        (self.root / "보고서.TXT").write_bytes(b"x" * 2048)
        (self.root / "README").write_bytes(b"x" * 7)
        (self.root / ".hidden").write_bytes(b"x")

        self.app = app_module.App(
            my_name="nc-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.refresh()

    def body(self, size=(104, 30)):
        return "\n".join(self.app._compose(size))


class TestMdirColumns(MdirListTestCase):
    def test_directories_show_subdir_marker(self):
        self.assertIn("[ SubDir ]", self.body())

    def test_parent_shows_up_dir_marker(self):
        self.app.local.navigate(self.root / "FONT")
        self.assertIn("[ Up-Dir ]", self.body())

    def test_size_uses_thousands_separators(self):
        text = self.body()
        self.assertIn("15,210", text)
        self.assertIn("1,054", text)

    def test_extension_is_a_separate_column_in_upper_case(self):
        text = self.body()
        self.assertIn("MERGEDIC", text)
        self.assertIn("EXE", text)
        # 이름 칸에는 확장자가 붙어 있지 않아야 한다
        self.assertNotIn("MERGEDIC.EXE", text)

    def test_date_and_time_columns(self):
        stamp = time.localtime((self.root / "HWP.CFG").stat().st_mtime)
        self.assertIn(time.strftime("%y-%m-%d", stamp), self.body())

    def test_summary_line_matches_mdir(self):
        """MDir 하단의 `19 File 6 Dir 1,901,416 Byte` 형식."""
        summary = self.app._pane_summary(self.app.local)
        self.assertIn("File", summary)
        self.assertIn("Dir", summary)
        self.assertIn("Byte", summary)
        # 숨김 파일을 뺀 파일 4개, 폴더 2개
        self.assertTrue(summary.startswith("4 File  2 Dir"), summary)

    def test_extensionless_file_has_an_empty_ext_column(self):
        self.assertIn("README", self.body())

    def test_line_widths_stay_exact_at_every_size(self):
        for size in ((80, 24), (100, 30), (120, 40), (140, 40), (40, 12)):
            for index, line in enumerate(self.app._compose(size)):
                self.assertEqual(
                    screen.width(line), size[0], "size=%s line %d" % (size, index)
                )

    def test_narrow_panes_fold_columns_instead_of_overflowing(self):
        """좁아지면 오른쪽 칸부터 접는다. 잘려 읽을 수 없게 두지 않는다."""
        self.assertEqual(app_module.App._columns(60), (True, True, True))
        # 시간 칸이 먼저 접히고, 다음이 날짜, 마지막이 확장자다
        self.assertEqual(app_module.App._columns(36), (True, True, False))
        self.assertEqual(app_module.App._columns(30), (True, False, False))
        self.assertEqual(app_module.App._columns(18), (False, False, False))

    def test_column_folding_is_monotonic(self):
        """폭이 넓어질수록 칸이 늘기만 해야 한다. 중간에 뒤집히면 화면이 튄다."""
        previous = (False, False, False)
        for inner in range(10, 80):
            current = app_module.App._columns(inner)
            for was, now in zip(previous, current):
                self.assertFalse(was and not now, "inner=%d 에서 칸이 줄었다" % inner)
            previous = current


class TestHiddenFiles(MdirListTestCase):
    def test_hidden_files_are_off_by_default(self):
        self.assertNotIn(".hidden", self.body())

    def test_toggle_shows_them(self):
        self.app.toggle_hidden()
        self.assertIn(".hidden", self.body())
        self.assertIn("보임", self.app.status)

    def test_toggle_twice_hides_again(self):
        self.app.toggle_hidden()
        self.app.toggle_hidden()
        self.assertNotIn(".hidden", self.body())


class TestMenuBar(MdirListTestCase):
    def open_menu(self):
        self.app._on_key("F9")
        self.assertIsInstance(self.app.modal, app_module.MenuBar)
        return self.app.modal

    def test_f9_and_9_both_open_it(self):
        self.open_menu()
        self.app.modal = None
        self.app._on_key("9")
        self.assertIsInstance(self.app.modal, app_module.MenuBar)

    def test_bar_shows_the_five_nc_menus(self):
        menu = self.open_menu()
        bar = menu.bar_line(104)
        for title in ("Left", "Files", "Commands", "Options", "Right"):
            self.assertIn(title, bar)

    def test_bar_is_exact_width(self):
        menu = self.open_menu()
        for cols in (60, 80, 104, 140):
            self.assertEqual(screen.width(menu.bar_line(cols)), cols, cols)

    def test_opens_on_files_like_nc(self):
        menu = self.open_menu()
        self.assertEqual(menu.menus[menu.menu][0], "Files")

    def test_arrows_move_between_menus(self):
        menu = self.open_menu()
        menu.key(term.LEFT)
        self.assertEqual(menu.menus[menu.menu][0], "Left")
        menu.key(term.RIGHT)
        menu.key(term.RIGHT)
        self.assertEqual(menu.menus[menu.menu][0], "Commands")

    def test_left_and_right_menus_configure_their_own_pane(self):
        menu = self.open_menu()
        menu.key(term.LEFT)  # Left
        labels = [l for l, _, _ in menu.menus[menu.menu][1]]
        self.assertIn("이름순 정렬", labels)
        self.assertIn("드라이브 바꾸기", labels)  # 로컬 패널에만 있다

        menu.menu = 4  # Right
        menu.menus = menu._build()
        labels = [l for l, _, _ in menu.menus[4][1]]
        self.assertIn("크기순 정렬", labels)
        self.assertNotIn("드라이브 바꾸기", labels)

    def test_separators_are_skipped_by_the_cursor(self):
        menu = self.open_menu()
        items = menu.menus[menu.menu][1]
        for _ in range(len(items) * 2):
            menu.key(term.DOWN)
            self.assertIsNotNone(items[menu.item][2], "구분선에 커서가 멈췄다")

    def test_enter_runs_the_action_and_closes(self):
        menu = self.open_menu()
        labels = [l for l, _, _ in menu.menus[menu.menu][1]]
        menu.item = labels.index("도움말")

        self.app._on_key(term.ENTER)
        self.assertIsInstance(self.app.modal, app_module.MessageDialog)

    def test_sort_from_the_left_menu_applies_to_the_local_pane(self):
        menu = self.open_menu()
        menu.key(term.LEFT)
        labels = [l for l, _, _ in menu.menus[menu.menu][1]]
        menu.item = labels.index("크기순 정렬")

        self.app._on_key(term.ENTER)
        self.assertEqual(self.app.local.sort_mode, fsops.SORT_SIZE)
        self.assertIn("크기", self.app.status)

    def test_options_menu_toggles_hidden_files(self):
        menu = self.open_menu()
        menu.menu = 3  # Options
        menu.menus = menu._build()
        menu.item = 0

        self.app._on_key(term.ENTER)
        self.assertTrue(self.app.show_hidden)

    def test_option_label_reflects_current_state(self):
        menu = self.open_menu()
        menu.menu = 3
        menu.menus = menu._build()
        self.assertEqual(menu.menus[3][1][0][0], "숨김 파일 보기")

        self.app.show_hidden = True
        menu.menus = menu._build()
        self.assertEqual(menu.menus[3][1][0][0], "숨김 파일 감추기")

    def test_esc_closes(self):
        self.open_menu()
        self.app._on_key(term.ESC)
        self.assertIsNone(self.app.modal)

    def test_quit_from_the_menu_works(self):
        menu = self.open_menu()
        labels = [l for l, _, _ in menu.menus[menu.menu][1]]
        menu.item = labels.index("끝내기")
        self.app._on_key(term.ENTER)
        self.assertFalse(self.app.running)

    def test_dropdown_floats_over_the_panes_without_erasing_them(self):
        """NC 처럼 상자 좌우의 패널 테두리가 남아야 한다."""
        self.open_menu()
        lines = self.app._compose((104, 30))

        row = screen._ANSI.sub("", lines[1])  # 패널 위 테두리와 상자가 겹치는 줄
        self.assertTrue(row.startswith(app_module.BOX_TL), row[:12])
        self.assertTrue(row.rstrip().endswith(app_module.BOX_TR), row[-12:])
        self.assertIn(app_module.BOX_TL, row[1:], "상자 테두리가 보이지 않는다")

    def test_menu_keeps_line_widths(self):
        self.open_menu()
        for size in ((80, 24), (104, 30), (140, 40)):
            for index, line in enumerate(self.app._compose(size)):
                self.assertEqual(
                    screen.width(line), size[0], "size=%s line %d" % (size, index)
                )

    def test_bar_replaces_the_header_row(self):
        self.open_menu()
        lines = self.app._compose((104, 30))
        self.assertIn("Files", lines[0])
        self.assertNotIn("MDirNet", lines[0])


class TestFunctionBarMatchesNc(MdirListTestCase):
    def test_nine_is_menu_like_nc(self):
        labels = dict(app_module._FKEYS)
        self.assertEqual(labels["9"], "메뉴")

    def test_bar_order_follows_the_reference(self):
        self.assertEqual(
            [l for _, l in app_module._FKEYS],
            ["도움", "연결", "보기", "폴더", "복사", "끊기", "만들", "삭제", "메뉴", "끝내"],
        )


class TestSplice(unittest.TestCase):
    """겹쳐 그리기. 이게 없으면 다이얼로그가 줄 전체를 지운다."""

    def test_preserves_total_width(self):
        base = "|" + "한글" * 5 + "|"
        for col in range(0, 10):
            out = screen.splice(base, col, "[X]")
            self.assertEqual(screen.width(out), screen.width(base), col)

    def test_keeps_both_sides(self):
        out = screen.splice("|abcdefghij|", 4, "##")
        plain = screen._ANSI.sub("", out)
        self.assertTrue(plain.startswith("|abc"))
        self.assertTrue(plain.endswith("ghij|"))

    def test_wide_char_on_the_boundary_becomes_a_space(self):
        out = screen.splice("|한글|", 2, "X")
        self.assertEqual(screen.width(out), screen.width("|한글|"))

    def test_insert_past_the_end_extends(self):
        out = screen.splice("|ab|", 10, "X")
        self.assertEqual(screen.width(out), 11)


if __name__ == "__main__":
    unittest.main()
