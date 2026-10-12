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

    def test_size_uses_readable_units(self):
        """GB/MB/KB 로 보여준다. 바이트 수를 그대로 쓰면 이름 칸이 좁아진다."""
        text = self.body()
        self.assertIn("14.9 KB", text)   # MERGEDIC.EXE 15,210 바이트
        self.assertIn("1.0 KB", text)    # HWP.CFG 1,054 바이트

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
        """상자를 그려도 그 좌우의 패널 내용이 남아야 한다."""
        self.open_menu()
        lines = [screen._ANSI.sub("", l) for l in self.app._compose((104, 30))]

        row = lines[1]  # 제목 줄에 상자 윗변이 겹친다
        self.assertIn("내 PC", row, "상자가 왼쪽 제목을 지웠다")
        self.assertIn(app_module.BOX_TL, row, "상자 테두리가 보이지 않는다")
        self.assertIn("상대", row, "상자가 오른쪽 제목을 지웠다")

        # 본문 줄에서도 상자 오른쪽에 패널 구분선이 남아야 한다
        body = next(l for l in lines[3:12] if app_module.BOX_V in l)
        self.assertIn(app_module.BOX_V, body.split(app_module.BOX_BR)[-1] or body)

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

    def test_six_is_rename_like_nc(self):
        """NC 의 6번은 Rename/Move 다."""
        self.assertEqual(dict(app_module._FKEYS)["6"], "이름")

    def test_connect_and_disconnect_are_not_on_the_bar(self):
        """연결과 끊기는 Commands 메뉴와 문자 키로만 둔다."""
        labels = [l for _, l in app_module._FKEYS]
        self.assertNotIn("연결", labels)
        self.assertNotIn("끊기", labels)

    def test_bar_order_follows_the_reference(self):
        self.assertEqual(
            [l for _, l in app_module._FKEYS],
            ["도움", "정렬", "보기", "폴더", "복사", "이름", "만들", "삭제", "메뉴", "끝내"],
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


class TestRename(MdirListTestCase):
    """NC 의 6번 자리(Rename/Move)를 되찾은 기능."""

    def open_rename(self, name):
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == name
        )
        self.app._on_key("6")
        self.assertIsInstance(self.app.modal, app_module.InputDialog)
        return self.app.modal

    def test_dialog_starts_with_the_current_name(self):
        dialog = self.open_rename("HWP.CFG")
        self.assertEqual(dialog.text, "HWP.CFG")

    def test_renames_a_file(self):
        dialog = self.open_rename("HWP.CFG")
        dialog.text = "새이름.CFG"
        dialog.key(term.ENTER)

        self.assertTrue((self.root / "새이름.CFG").is_file())
        self.assertFalse((self.root / "HWP.CFG").exists())
        self.assertIn("새이름.CFG", self.app.status)

    def test_renames_a_directory(self):
        dialog = self.open_rename("FONT")
        dialog.text = "글꼴"
        dialog.key(term.ENTER)
        self.assertTrue((self.root / "글꼴").is_dir())

    def test_refuses_to_overwrite_an_existing_name(self):
        """POSIX 의 rename 은 대상이 있으면 조용히 지운다. 반드시 막아야 한다."""
        dialog = self.open_rename("HWP.CFG")
        dialog.text = "README"
        dialog.key(term.ENTER)

        self.assertIsInstance(self.app.modal, app_module.MessageDialog)
        self.assertTrue((self.root / "HWP.CFG").is_file(), "원본이 사라졌다")
        self.assertEqual((self.root / "README").read_bytes(), b"x" * 7, "대상이 덮여졌다")

    def test_refuses_path_separators(self):
        dialog = self.open_rename("HWP.CFG")
        dialog.text = "sub/nope.cfg"
        dialog.key(term.ENTER)

        self.assertIsInstance(self.app.modal, app_module.MessageDialog)
        self.assertTrue((self.root / "HWP.CFG").is_file())

    def test_unchanged_name_does_nothing(self):
        self.open_rename("HWP.CFG")
        # 앱을 거쳐 눌러야 모달이 닫힌다 (다이얼로그는 닫으라고 알리기만 한다)
        self.app._on_key(term.ENTER)
        self.assertTrue((self.root / "HWP.CFG").is_file())
        self.assertIsNone(self.app.modal)

    def test_dotdot_cannot_be_renamed(self):
        self.app.local.navigate(self.root / "FONT")
        self.app.local.cursor = 0  # '..'
        self.app._on_key("6")
        self.assertIsNone(self.app.modal)
        self.assertIn("커서를 두세요", self.app.status)

    def test_menu_has_rename_too(self):
        self.app._on_key("F9")
        labels = [l for l, _, _ in self.app.modal.menus[1][1]]
        self.assertIn("이름 바꾸기", labels)


class TestHiddenToggleKey(MdirListTestCase):
    """'.' 키로 숨김 파일을 보였다 안 보였다 한다."""

    def test_dot_toggles(self):
        self.assertNotIn(".hidden", self.body())

        self.app._on_key(".")
        self.assertIn(".hidden", self.body())

        self.app._on_key(".")
        self.assertNotIn(".hidden", self.body())

    def test_dot_reports_the_state(self):
        self.app._on_key(".")
        self.assertIn("보임", self.app.status)
        self.app._on_key(".")
        self.assertIn("감춤", self.app.status)

    def test_dot_stays_typable_in_an_input_box(self):
        """마스크에 `*.txt` 를 쳐야 하므로 입력창에서는 토글되면 안 된다."""
        self.app._on_key("+")
        self.app.modal.text = ""
        for ch in "*.txt":
            self.app._on_key(ch)

        self.assertEqual(self.app.modal.text, "*.txt")
        self.assertFalse(self.app.show_hidden, "입력 중에 토글되었다")

    def test_dot_is_typable_when_renaming(self):
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == "README"
        )
        self.app._on_key("6")
        self.app.modal.text = "README"
        for ch in ".md":
            self.app._on_key(ch)
        self.assertEqual(self.app.modal.text, "README.md")
        self.assertFalse(self.app.show_hidden)

    def test_option_menu_shows_the_dot_hint(self):
        self.app._on_key("F9")
        menu = self.app.modal
        menu.menu = 3
        menu.menus = menu._build()
        self.assertEqual(menu.menus[3][1][0][1], ".")

    def test_hidden_directories_are_filtered_too(self):
        (self.root / ".git").mkdir()
        self.app.local.refresh()
        self.assertNotIn(".git", self.body())

        self.app._on_key(".")
        self.assertIn(".git", self.body())


class TestOverlayDoesNotInheritStyles(MdirListTestCase):
    """커서가 놓인 줄의 선택색이 위에 겹친 상자로 새어나가면 안 된다.

    커서 줄은 REVERSE 로 시작해 줄 끝에서 RESET 된다. 그 중간에 메뉴 상자를
    겹쳐 쓰면서 스타일을 끊지 않으면 상단 메뉴까지 반전으로 물든다.
    """

    def test_splice_resets_before_the_inserted_text(self):
        row = term.BAR + " note.txt   1,054 " + term.RESET
        out = screen.splice(row, 5, "| 메뉴 |")

        before_box = out.split("| 메뉴")[0]
        self.assertTrue(
            before_box.endswith(term.RESET),
            "상자 앞에서 스타일을 끊지 않았습니다: %r" % before_box,
        )

    def test_splice_restores_the_style_after_the_box(self):
        row = term.BAR + " note.txt   1,054 " + term.RESET
        out = screen.splice(row, 5, "| 메뉴 |")
        self.assertIn(term.BAR, out.split("메뉴 |")[1], "뒷부분 색이 끊겼습니다")

    def test_menu_box_is_not_reversed_when_it_overlaps_the_cursor_row(self):
        """실제 화면 조립으로 확인한다. 커서를 상자가 걸치는 줄에 둔다."""
        self.app.local.cursor = 3
        self.app._on_key("F9")
        lines = self.app._compose((104, 30))

        # 상자가 그려진 줄들을 훑어, 상자 테두리 앞에 RESET 이 오는지 본다
        checked = 0
        for line in lines[1:12]:
            if app_module.BOX_V not in line and app_module.BOX_TL not in line:
                continue
            for piece in ("| 파일 보기", "| 복사", "|--------"):
                if piece in line:
                    head = line.split(piece)[0]
                    self.assertNotIn(
                        term.BAR,
                        head.split(term.RESET)[-1],
                        "상자가 선택색을 물려받았습니다: %r" % line[:70],
                    )
                    checked += 1
        self.assertGreater(checked, 0, "검사할 상자 줄을 찾지 못했습니다")

    def test_dialog_box_is_not_reversed_over_the_cursor_row(self):
        self.app.local.cursor = 5
        self.app._on_key("1")  # 도움말
        for line in self.app._compose((104, 30)):
            if "도움말" in line:
                head = line.split("도움말")[0]
                self.assertNotIn(term.BAR, head.split(term.RESET)[-1], line[:60])


class TestRightArrowEntersFolder(MdirListTestCase):
    """오른쪽 화살표로 폴더 안으로 들어간다."""

    def cursor_on(self, name):
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == name
        )

    def test_right_arrow_enters_a_directory(self):
        self.cursor_on("FONT")
        self.app._on_key(term.RIGHT)
        self.assertEqual(self.app.local.path, self.root / "FONT")

    def test_l_also_enters(self):
        self.cursor_on("한글폴더")
        self.app._on_key("l")
        self.assertEqual(self.app.local.path, self.root / "한글폴더")

    def test_right_arrow_on_a_file_does_nothing(self):
        self.cursor_on("HWP.CFG")
        self.app._on_key(term.RIGHT)
        self.assertEqual(self.app.local.path, self.root)

    def test_right_arrow_on_dotdot_does_not_go_up(self):
        """위로 가는 것은 왼쪽 화살표의 몫이다. 방향이 어긋나면 안 된다."""
        self.app.local.navigate(self.root / "FONT")
        self.app.local.cursor = 0  # '..'
        self.app._on_key(term.RIGHT)
        self.assertEqual(self.app.local.path, self.root / "FONT")

    def test_left_arrow_still_goes_up(self):
        self.app.local.navigate(self.root / "FONT")
        self.app._on_key(term.LEFT)
        self.assertEqual(self.app.local.path, self.root)

    def test_round_trip_with_arrows(self):
        self.cursor_on("FONT")
        self.app._on_key(term.RIGHT)
        self.app._on_key(term.LEFT)
        self.assertEqual(self.app.local.path, self.root)

    def test_enter_still_works_the_same(self):
        self.cursor_on("FONT")
        self.app._on_key(term.ENTER)
        self.assertEqual(self.app.local.path, self.root / "FONT")

    def test_help_mentions_the_arrow(self):
        self.app._on_key("1")
        self.assertIn("폴더 안으로", "\n".join(app_module._HELP_LINES))


class TestSlashOpensMenu(MdirListTestCase):
    """'/' 도 9번처럼 상단 메뉴를 연다."""

    def test_slash_opens_the_menu(self):
        self.app._on_key("/")
        self.assertIsInstance(self.app.modal, app_module.MenuBar)

    def test_slash_matches_nine_and_f9(self):
        opened = []
        for key in ("9", "F9", "/"):
            self.app.modal = None
            self.app._on_key(key)
            opened.append(type(self.app.modal).__name__)
        self.assertEqual(opened, ["MenuBar"] * 3)

    def test_slash_closes_the_menu_again(self):
        self.app._on_key("/")
        self.app._on_key("/")
        self.assertIsNone(self.app.modal)

    def test_slash_is_typable_in_an_input_box(self):
        """마스크에 경로 같은 것을 칠 수 있어야 한다. 입력창에서는 메뉴가 열리면 안 된다."""
        self.app._on_key("+")
        self.app.modal.text = ""
        for ch in "a/b":
            self.app._on_key(ch)
        self.assertEqual(self.app.modal.text, "a/b")
        self.assertIsInstance(self.app.modal, app_module.InputDialog)

    def test_slash_does_not_open_the_drive_dialog(self):
        self.app._on_key("/")
        self.assertNotIsInstance(self.app.modal, app_module.DriveDialog)

    def test_help_shows_slash_next_to_the_menu(self):
        self.app._on_key("1")
        self.assertIn("상단 메뉴 (/)", "\n".join(app_module._HELP_LINES))


class TestFixedColumnsVariableName(MdirListTestCase):
    """확장자/크기/날짜/시간 칸은 고정, 이름 칸만 창 폭에 따라 늘고 준다."""

    def name_width(self, inner):
        show_ext, show_date, show_time = app_module.App._columns(inner)
        used = 2 + app_module.SIZE_COL
        used += (app_module.EXT_COL + 1) if show_ext else 0
        used += (app_module.DATE_COL + 1) if show_date else 0
        used += (app_module.TIME_COL + 1) if show_time else 0
        return max(inner - used, app_module.NAME_MIN)

    @property
    def min_full_cols(self):
        """모든 칸이 보이는 최소 창 폭. 상수에서 계산한다."""
        inner = app_module.FIXED_COLS + app_module.NAME_MIN
        return (inner + 2) * 2

    def test_all_columns_stay_at_normal_widths(self):
        for cols in (self.min_full_cols, 100, 120, 160, 200):
            inner = cols // 2 - 2
            self.assertEqual(
                app_module.App._columns(inner), (True, True, True), "cols=%d" % cols
            )

    def test_name_column_grows_with_the_window(self):
        widths = [self.name_width(cols // 2 - 2) for cols in (80, 100, 120, 160, 200)]
        self.assertEqual(widths, sorted(widths))
        self.assertEqual(len(set(widths)), len(widths), "폭이 늘어도 이름이 그대로다")

    def test_name_absorbs_every_extra_column(self):
        """창을 20칸 넓히면 각 패널 이름 칸이 10칸씩 늘어야 한다."""
        a = self.name_width(100 // 2 - 2)
        b = self.name_width(120 // 2 - 2)
        self.assertEqual(b - a, 10)

    def test_fixed_columns_never_change_width(self):
        """오른쪽 칸들은 어떤 창 폭에서도 같은 자리에 있어야 한다."""
        seen = set()
        for cols in (self.min_full_cols, 100, 140, 200):
            lines = self.app._compose((cols, 30))
            for line in lines:
                plain = screen._ANSI.sub("", line)
                if "[ SubDir ]" in plain:
                    # '[ SubDir ]' 오른쪽에 남는 칸 수 (날짜+시간 자리)
                    left_pane = plain[: cols // 2]
                    after = left_pane.split("[ SubDir ]", 1)[1]
                    seen.add(screen.width(after.rstrip("|")))
                    break
        self.assertEqual(len(seen), 1, "창 폭에 따라 오른쪽 칸 폭이 달라졌다: %s" % seen)

    def test_long_name_is_truncated_not_the_columns(self):
        long_name = "아주아주아주아주아주긴한글파일이름입니다정말로깁니다.bin"
        (self.root / long_name).write_bytes(b"z" * 5)
        self.app.local.refresh()

        for cols in (self.min_full_cols, 100, 140):
            body = "\n".join(self.app._compose((cols, 30)))
            self.assertIn("~", body, "긴 이름이 잘렸다는 표시가 없다")
            self.assertIn("5 B", body, "크기 칸이 밀렸다")

    def test_size_column_fits_every_magnitude(self):
        from mdirnet import fsops

        for n in (0, 999, 1024, 15210, 3_500_000, 2**31, 1023 * 1024**3):
            self.assertLessEqual(
                screen.width(fsops.human_size(n)), app_module.SIZE_COL, n
            )

    def test_subdir_marker_fits_the_size_column(self):
        for marker in ("[ SubDir ]", "[ Up-Dir ]"):
            self.assertLessEqual(screen.width(marker), app_module.SIZE_COL)

    def test_folding_only_happens_below_the_computed_threshold(self):
        """임계값 이상이면 접히지 않는다. 그 아래는 물리적으로 담을 수 없다."""
        full = self.min_full_cols
        self.assertEqual(app_module.App._columns(full // 2 - 2), (True, True, True))
        self.assertNotEqual(app_module.App._columns((full - 4) // 2 - 2), (True, True, True))

    def test_threshold_is_a_sane_terminal_width(self):
        """임계값이 터무니없이 크면 대부분의 창에서 칸이 접혀 고정이 무의미해진다."""
        self.assertLessEqual(self.min_full_cols, 90, self.min_full_cols)

    def test_lines_stay_exact_at_every_width(self):
        long_name = "긴" * 60 + ".txt"
        (self.root / long_name).write_bytes(b"z")
        self.app.local.refresh()
        for cols in range(40, 201, 9):
            for line in self.app._compose((cols, 24)):
                self.assertEqual(screen.width(line), cols, "cols=%d" % cols)


class TestColumnWidthsFitTheirContent(unittest.TestCase):
    """칸 폭이 내용보다 좁으면 조용히 잘린다. 시각과 확장자가 실제로 그랬다."""

    def test_time_column_fits_two_digit_hours(self):
        """`11:14a` 는 6칸이다. 5로 잡으면 `11:1~` 로 잘린다."""
        import time as _time

        from mdirnet import fsops

        longest = 0
        for hour in range(24):
            stamp = _time.mktime((2026, 7, 28, hour, 59, 0, 0, 0, -1))
            longest = max(longest, screen.width(fsops.mdir_time(stamp)))
        self.assertEqual(longest, 6)
        self.assertGreaterEqual(app_module.TIME_COL, longest)

    def test_date_column_fits(self):
        import time as _time

        from mdirnet import fsops

        stamp = _time.mktime((2026, 12, 31, 0, 0, 0, 0, 0, -1))
        self.assertGreaterEqual(app_module.DATE_COL, screen.width(fsops.mdir_date(stamp)))

    def test_extension_column_fits_common_four_letter_extensions(self):
        from mdirnet import fsops

        for name in ("index.html", "data.json", "photo.jpeg", "표.xlsx", "a.docx"):
            _, ext = fsops.split_name(name)
            self.assertLessEqual(
                screen.width(ext.upper()), app_module.EXT_COL, "%s -> %s" % (name, ext)
            )

    def test_size_column_fits_the_largest_readable_size(self):
        from mdirnet import fsops

        biggest = max(
            screen.width(fsops.human_size(1024 ** e * m))
            for e in range(5)
            for m in (1, 512, 1023)
        )
        self.assertGreaterEqual(app_module.SIZE_COL, biggest)


class TestNothingIsTruncatedInFixedColumns(MdirListTestCase):
    def setUp(self):
        super().setUp()
        import os
        import time as _time

        # 두 자리 시각으로 맞춘다 (한 자리면 시간 칸 문제가 드러나지 않는다)
        stamp = _time.mktime((2026, 7, 28, 11, 14, 0, 0, 0, -1))
        (self.root / "index.html").write_bytes(b"x" * 4200)
        for path in self.root.iterdir():
            os.utime(path, (stamp, stamp))
        self.app.local.refresh()

    def test_time_is_shown_in_full(self):
        body = self.body((100, 24))
        self.assertIn("11:14a", body)
        self.assertNotIn("11:1~", body)

    def test_four_letter_extension_is_shown_in_full(self):
        body = self.body((100, 24))
        self.assertIn("HTML", body)
        self.assertNotIn("HT~", body)

    def test_still_full_at_the_narrowest_width_that_shows_everything(self):
        body = self.body((82, 24))
        self.assertIn("11:14a", body)
        self.assertIn("HTML", body)


class TestMdirFrame(MdirListTestCase):
    """MDir 처럼 바깥 벽 없이 가로줄과 가운데 구분선만 쓴다."""

    def plain(self, cols=100, rows=24):
        return [screen._ANSI.sub("", l) for l in self.app._compose((cols, rows))]

    @staticmethod
    def _char_at_column(text, column):
        """표시 칸 기준으로 그 자리의 글자를 꺼낸다.

        한글이 섞이면 문자열 인덱스와 표시 칸이 어긋나므로 폭을 세며 찾아야 한다.
        """
        used = 0
        for ch in text:
            width = screen.char_width(ch)
            if used <= column < used + width:
                return ch
            used += width
        return " "  # 줄이 짧으면 그 자리는 빈칸이다

    def test_no_outer_walls(self):
        """왼쪽 끝과 오른쪽 끝에 벽이 없어야 한다. 그만큼 내용이 넓어진다."""
        cols = 100
        for line in self.plain(cols)[1:-2]:
            self.assertNotEqual(self._char_at_column(line, 0), app_module.BOX_V, line[:40])
            self.assertNotEqual(
                self._char_at_column(line, cols - 1), app_module.BOX_V, line[-40:]
            )

    def test_exactly_one_vertical_divider_per_row(self):
        for line in self.plain()[1:-2]:
            if not line.strip():
                continue
            self.assertLessEqual(line.count(app_module.BOX_V), 1, line[:60])

    def test_top_and_bottom_are_two_rows_each(self):
        """제목 줄 + 가로줄, 가로줄 + 요약 줄."""
        lines = self.plain()
        self.assertIn("내 PC", lines[1])          # 제목 줄
        self.assertIn("+", lines[2])              # 가로줄
        self.assertEqual(set(lines[2]) - {"=", "+"}, set(), lines[2])

        self.assertIn("+", lines[-4])             # 아래 가로줄
        self.assertEqual(set(lines[-4]) - {"=", "+"}, set(), lines[-4])
        self.assertIn("File", lines[-3])          # 요약 줄

    def test_rules_have_a_single_joint(self):
        lines = self.plain()
        for row in (lines[2], lines[-4]):
            self.assertEqual(row.count("+"), 1, row)

    def test_joint_lines_up_with_the_divider(self):
        """가로줄의 `+` 와 본문의 `|` 가 같은 칸에 있어야 세로로 이어져 보인다."""
        lines = self.plain()
        rule = lines[2]
        joint_col = screen.width(rule[: rule.index("+")])

        for row in lines[1:-2]:
            if app_module.BOX_V in row:
                self.assertEqual(
                    screen.width(row[: row.index(app_module.BOX_V)]), joint_col, row[:60]
                )
            elif "+" in row:
                self.assertEqual(screen.width(row[: row.index("+")]), joint_col, row[:60])

    def test_removing_outer_walls_gives_two_columns_to_content(self):
        cols = 100
        body = [l for l in self.plain(cols) if app_module.BOX_V in l][2]
        self.assertEqual(body.count(app_module.BOX_V), 1)
        self.assertEqual(screen.width(body) - 1, cols - 1)

    def test_focused_pane_title_is_highlighted(self):
        self.app.focus = self.app.left
        title = self.app._compose((100, 24))[1]
        self.assertIn(term.BAR, title.split("|")[0])

    def test_focus_marker_moves_with_tab(self):
        self.app.session = _StubForFocus()
        self.app.focus = self.app.left
        before = self.app._compose((100, 24))[1]
        self.app._on_key(term.TAB)
        after = self.app._compose((100, 24))[1]
        self.assertNotEqual(before, after, "Tab 을 눌러도 강조가 그대로다")

    def test_widths_stay_exact_at_every_size(self):
        for cols in range(40, 201, 7):
            for line in self.app._compose((cols, 24)):
                self.assertEqual(screen.width(line), cols, "cols=%d" % cols)


class _StubForFocus:
    peer_name = "stub"
    peer_host = "127.0.0.1"

    def close(self, reason=None):
        pass


class TestTighterFrame(MdirListTestCase):
    """왼쪽 여백 제거와 `=` 가로줄."""

    def plain(self, cols=100, rows=26):
        return [screen._ANSI.sub("", l) for l in self.app._compose((cols, rows))]

    def rules(self, lines):
        return [i for i, r in enumerate(lines) if r and set(r) <= {"=", "+"} and "+" in r]

    def test_left_pane_starts_at_column_zero(self):
        """왼쪽은 화면 끝이라 여백이 필요없다. 제목이 0번 칸에서 시작한다.

        본문 줄의 0번 칸은 선택 표시 자리라 표시가 없으면 빈칸이다. 그쪽은
        test_selection_mark_is_the_first_column_on_the_left 가 본다.
        """
        lines = self.plain()
        rules = self.rules(lines)
        title = lines[rules[0] - 1]
        self.assertNotEqual(title[0], " ", title[:20])

    def test_selection_mark_is_the_first_column_on_the_left(self):
        self.app.local.selected = {"HWP.CFG"}
        self.app.local.refresh()
        marked = [l for l in self.plain() if "HWP" in l][0]
        self.assertTrue(marked.startswith("*"), marked[:20])

    def test_right_pane_starts_right_after_the_divider(self):
        """오른쪽도 구분선 바로 다음 칸이 선택 표시 자리다. 왼쪽과 모양이 같다."""
        self.app._on_key("=")
        self.app.right.navigate(self.root)
        self.app.right.selected = {"HWP.CFG"}
        self.app.right.refresh()

        lines = self.plain()
        marked = [l for l in lines if l.count("HWP") and app_module.BOX_V in l][0]
        after = marked.split(app_module.BOX_V, 1)[1]
        self.assertTrue(after.startswith("*"), repr(after[:12]))

    def test_horizontal_rule_is_one_row_each(self):
        """`=` 가 두 줄처럼 보이므로 위아래 각각 한 줄이면 된다."""
        lines = self.plain()
        rules = self.rules(lines)
        self.assertEqual(len(rules), 2, rules)

    def test_top_and_bottom_rules_are_identical(self):
        lines = self.plain()
        rules = self.rules(lines)
        self.assertEqual(lines[rules[0]], lines[rules[1]])

    def test_both_panes_use_the_same_name_width(self):
        """여백을 없앴으므로 두 패널의 이름 칸이 같아야 한다.

        가운데 구분선 때문에 한쪽이 한 칸 넓을 수는 있다(홀수 폭). 그 이상
        차이가 나면 어느 한쪽에 여백이 남아 있다는 뜻이다.
        """
        self.app._on_key("=")
        self.app.right.navigate(self.root)
        self.app.right.refresh()

        lines = self.plain(cols=100)
        row = [l for l in lines if "HWP" in l][0]
        left, right = row.split(app_module.BOX_V, 1)
        self.assertEqual(left.index("HWP"), right.index("HWP"))

    def test_widths_stay_exact(self):
        for cols in range(40, 201, 9):
            for line in self.app._compose((cols, 26)):
                self.assertEqual(screen.width(line), cols, "cols=%d" % cols)

    def test_body_takes_everything_but_the_frame(self):
        """머리글/상태줄/힌트줄 3 + 제목·가로줄·가로줄·요약 4 를 뺀 나머지."""
        self.app._compose((100, 30))
        self.assertEqual(self.app._body_height, 30 - 3 - 4)
