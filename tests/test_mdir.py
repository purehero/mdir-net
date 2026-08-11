# MDir 관습을 따라 추가한 기능들 — 기능키 바, 마스크 선택, 뷰어, 만들기·삭제·정렬

import queue
import tempfile
import unittest
from pathlib import Path

from mdirnet import fsops, session
from mdirnet.ui import app as app_module
from mdirnet.ui import screen, term

from .helpers import make_shares, only_name, rpath




class MdirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

        (self.root / "문서").mkdir()
        (self.root / "a.txt").write_bytes(b"1" * 100)
        (self.root / "b.TXT").write_bytes(b"2" * 300)
        (self.root / "c.jpg").write_bytes(b"3" * 200)
        (self.root / "메모.txt").write_bytes("한 줄\n두 줄\n세 줄\n".encode("utf-8"))
        (self.root / "blob.bin").write_bytes(bytes(range(256)))

        self.app = app_module.App(
            my_name="mdir-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.refresh()

    def cursor_to(self, name):
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == name
        )


class TestFunctionKeyBar(MdirTestCase):
    def test_bar_shows_all_ten_labels(self):
        bar = self.app._fkey_bar(100)
        for number, label in app_module._FKEYS:
            self.assertIn(label, bar, number)

    def test_bar_is_exactly_terminal_width(self):
        for cols in (70, 80, 100, 120, 200):
            self.assertEqual(screen.width(self.app._fkey_bar(cols)), cols, cols)

    def test_narrow_terminal_falls_back_without_overflowing(self):
        for cols in (40, 50, 69):
            bar = self.app._fkey_bar(cols)
            self.assertEqual(screen.width(bar), cols, cols)

    def test_bar_appears_in_the_composed_screen(self):
        body = "\n".join(self.app._compose((100, 30)))
        self.assertIn("복사", body)
        self.assertIn("메뉴", body)  # NC 처럼 9번은 메뉴다
        self.assertIn("끝내", body)


class TestSorting(MdirTestCase):
    def test_cycle_goes_name_ext_size_date_and_wraps(self):
        pane = self.app.local
        self.assertEqual(pane.sort_mode, fsops.SORT_NAME)
        self.assertEqual(pane.cycle_sort(), "확장자")
        self.assertEqual(pane.cycle_sort(), "크기")
        self.assertEqual(pane.cycle_sort(), "날짜")
        self.assertEqual(pane.cycle_sort(), "이름")

    def test_s_reports_the_new_mode(self):
        self.app._on_key("s")
        self.assertIn("확장자", self.app.status)

    def test_menu_can_set_a_sort_mode_directly(self):
        """NC 의 Left/Right 메뉴처럼 기준을 직접 고를 수 있다."""
        self.app.set_sort(self.app.local, fsops.SORT_SIZE)
        self.assertEqual(self.app.local.sort_mode, fsops.SORT_SIZE)
        self.assertIn("크기", self.app.status)

    def test_directories_stay_on_top_in_every_mode(self):
        for mode in fsops.SORT_ORDER:
            ordered = fsops.sort_entries(fsops.list_dir(self.root), mode)
            dirs = [i for i, e in enumerate(ordered) if e["is_dir"]]
            files = [i for i, e in enumerate(ordered) if not e["is_dir"]]
            self.assertTrue(max(dirs) < min(files), mode)

    def test_size_sort_is_largest_first(self):
        ordered = fsops.sort_entries(fsops.list_dir(self.root), fsops.SORT_SIZE)
        sizes = [e["size"] for e in ordered if not e["is_dir"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_ext_sort_groups_by_extension(self):
        ordered = fsops.sort_entries(fsops.list_dir(self.root), fsops.SORT_EXT)
        names = [e["name"] for e in ordered if not e["is_dir"]]
        # bin → jpg → txt 순. b.TXT 도 대소문자 구분 없이 txt 무리에 든다
        self.assertEqual(names[0], "blob.bin")
        self.assertEqual(names[1], "c.jpg")
        self.assertEqual(set(names[2:]), {"a.txt", "b.TXT", "메모.txt"})

    def test_hidden_file_has_no_extension(self):
        self.assertEqual(fsops._suffix(".gitignore"), "")
        self.assertEqual(fsops._suffix("a.tar.gz"), "gz")
        self.assertEqual(fsops._suffix("noext"), "")

    def test_sort_mode_survives_a_refresh(self):
        self.app.local.sort_mode = fsops.SORT_SIZE
        self.app.local.refresh()
        sizes = [e["size"] for e in self.app.local.entries if not e["is_dir"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


class TestMaskSelection(MdirTestCase):
    def test_plus_opens_input_dialog(self):
        self.app._on_key("+")
        self.assertIsInstance(self.app.modal, app_module.InputDialog)
        self.assertIn("*.txt", "\n".join(self.app.modal.lines(60)))

    def test_mask_selects_case_insensitively(self):
        count = self.app.local.select_mask("*.txt")
        self.assertEqual(count, 3)  # a.txt, b.TXT, 메모.txt
        self.assertEqual(self.app.local.selected, {"a.txt", "b.TXT", "메모.txt"})

    def test_mask_deselect_removes_only_matches(self):
        self.app.local.select_mask("*")
        before = set(self.app.local.selected)
        self.app.local.select_mask("*.txt", add=False)
        self.assertEqual(before - self.app.local.selected, {"a.txt", "b.TXT", "메모.txt"})

    def test_mask_with_no_match_reports_it(self):
        self.app._on_key("+")
        self.app.modal.text = "*.zip"
        self.app.modal.key(term.ENTER)
        self.assertIn("맞는 항목이 없습니다", self.app.status)

    def test_full_flow_through_the_dialog(self):
        self.app._on_key("+")
        for ch in "jpg":
            self.app.modal.key(ch)  # 초기값 '*.' 뒤에 이어 입력된다
        self.assertEqual(self.app.modal.text, "*.jpg")
        self.assertTrue(self.app.modal.key(term.ENTER), "Enter 는 다이얼로그를 닫아야 한다")
        self.assertEqual(self.app.local.selected, {"c.jpg"})
        self.assertIn("1개 선택", self.app.status)

    def test_insert_key_toggles_like_space(self):
        self.cursor_to("c.jpg")
        self.app._on_key(term.INSERT)
        self.assertIn("c.jpg", self.app.local.selected)

    def test_mask_never_selects_dotdot(self):
        self.app.local.select_mask("*")
        self.assertNotIn("..", self.app.local.selected)


class TestInputDialog(unittest.TestCase):
    def test_esc_cancels_without_submitting(self):
        got = []
        dialog = app_module.InputDialog("t", "p", got.append, initial="abc")
        self.assertTrue(dialog.key(term.ESC))
        self.assertEqual(got, [])

    def test_q_is_typed_not_treated_as_quit(self):
        """q 로 취소하면 파일 이름에 q 를 못 넣는다. Esc 만 취소여야 한다."""
        dialog = app_module.InputDialog("t", "p", lambda _: None)
        self.assertFalse(dialog.key("q"))
        self.assertEqual(dialog.text, "q")

    def test_backspace_and_space(self):
        dialog = app_module.InputDialog("t", "p", lambda _: None, initial="ab")
        dialog.key(term.BACKSPACE)
        dialog.key(term.SPACE)
        dialog.key("c")
        self.assertEqual(dialog.text, "a c")

    def test_empty_input_does_not_submit(self):
        got = []
        dialog = app_module.InputDialog("t", "p", got.append)
        self.assertTrue(dialog.key(term.ENTER))
        self.assertEqual(got, [])


class TestViewer(MdirTestCase):
    def test_f3_opens_text_viewer(self):
        self.cursor_to("메모.txt")
        self.app._on_key("F3")

        self.assertIsInstance(self.app.modal, app_module.ViewerDialog)
        self.assertFalse(self.app.modal.binary)
        self.assertEqual(self.app.modal.body, ["한 줄", "두 줄", "세 줄"])

    def test_binary_file_is_shown_as_hex(self):
        self.cursor_to("blob.bin")
        self.app._on_key("F3")

        viewer = self.app.modal
        self.assertTrue(viewer.binary)
        self.assertIn("00000000", viewer.body[0])
        self.assertIn("00 01 02", viewer.body[0])

    def test_directory_is_refused(self):
        self.cursor_to("문서")
        self.app._on_key("F3")
        self.assertIsNone(self.app.modal)
        self.assertIn("폴더는 볼 수 없습니다", self.app.status)

    def test_viewer_scrolls_and_clamps(self):
        many = self.root / "많은줄.txt"
        many.write_bytes(b"".join(("line %d\n" % i).encode() for i in range(200)))
        self.app.local.refresh()
        self.cursor_to("많은줄.txt")
        self.app._on_key("F3")

        viewer = self.app.modal
        viewer.max_body = 20
        viewer.lines(60)

        viewer.key(term.PGDN)
        self.assertGreater(viewer.offset, 0)

        for _ in range(100):
            viewer.key(term.PGDN)
        viewer.lines(60)
        self.assertLessEqual(viewer.offset, len(viewer.body))

        viewer.key(term.HOME_KEY)
        self.assertEqual(viewer.offset, 0)

        viewer.key(term.UP)
        viewer.lines(60)
        self.assertEqual(viewer.offset, 0)  # 위로 더 못 간다

    def test_q_closes_viewer(self):
        self.cursor_to("메모.txt")
        self.app._on_key("F3")
        self.app._on_key("q")
        self.assertIsNone(self.app.modal)
        self.assertTrue(self.app.running, "뷰어를 닫는 q 가 앱을 종료시키면 안 된다")

    def test_viewer_overlay_keeps_line_widths(self):
        self.cursor_to("blob.bin")
        self.app._on_key("F3")
        for size in ((80, 24), (120, 40), (100, 30)):
            for line in self.app._compose(size):
                self.assertEqual(screen.width(line), size[0])

    def test_truncation_is_reported(self):
        viewer = app_module.ViewerDialog("big.txt", b"abc\n", 999999)
        viewer.max_body = 18
        self.assertIn("앞", "\n".join(viewer.lines(70)))


class TestMkdir(MdirTestCase):
    def test_f7_opens_input_and_creates_directory(self):
        self.app._on_key("F7")
        self.assertIsInstance(self.app.modal, app_module.InputDialog)

        for ch in "새폴더":
            self.app.modal.key(ch)
        self.app.modal.key(term.ENTER)

        self.assertTrue((self.root / "새폴더").is_dir())
        self.assertIn("새폴더", self.app.status)

    def test_path_separator_is_rejected(self):
        self.app._on_key("F7")
        for ch in "a/b":
            self.app.modal.key(ch)
        self.app.modal.key(term.ENTER)

        self.assertIsInstance(self.app.modal, app_module.MessageDialog)
        self.assertFalse((self.root / "a").exists())

    def test_duplicate_name_reports_error(self):
        self.app._on_key("F7")
        for ch in "문서":
            self.app.modal.key(ch)
        self.app.modal.key(term.ENTER)
        self.assertIsInstance(self.app.modal, app_module.MessageDialog)


class TestDelete(MdirTestCase):
    def test_f8_asks_before_deleting(self):
        self.cursor_to("a.txt")
        self.app._on_key("F8")

        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)
        rendered = "\n".join(self.app.modal.lines(60))
        self.assertIn("a.txt", rendered)
        self.assertIn("되돌릴 수 없습니다", rendered)
        self.assertIn("내 PC", rendered)
        self.assertTrue((self.root / "a.txt").exists(), "묻기만 하고 아직 지우면 안 된다")

    def test_confirming_deletes_the_file(self):
        self.cursor_to("a.txt")
        self.app._on_key("F8")
        self.app._on_key("y")

        self.assertFalse((self.root / "a.txt").exists())
        self.assertIn("1개 항목을 지웠습니다", self.app.status)

    def test_declining_keeps_the_file(self):
        self.cursor_to("a.txt")
        self.app._on_key("F8")
        self.app._on_key("n")
        self.assertTrue((self.root / "a.txt").exists())

    def test_deletes_directory_recursively_with_warning(self):
        nested = self.root / "문서" / "안쪽"
        nested.mkdir()
        (nested / "깊은.txt").write_bytes(b"x")
        self.app.local.refresh()

        self.cursor_to("문서")
        self.app._on_key("F8")
        self.assertIn("안에 든 것까지", "\n".join(self.app.modal.lines(60)))
        self.app._on_key("y")

        self.assertFalse((self.root / "문서").exists())

    def test_deletes_every_selected_item(self):
        self.app.local.selected = {"a.txt", "c.jpg"}
        self.app._on_key("F8")
        self.app._on_key("y")

        self.assertFalse((self.root / "a.txt").exists())
        self.assertFalse((self.root / "c.jpg").exists())
        self.assertTrue((self.root / "b.TXT").exists())

    def test_delete_key_works_like_f8(self):
        self.cursor_to("a.txt")
        self.app._on_key(term.DELETE)
        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)

    def test_nothing_selected_and_cursor_on_dotdot_is_refused(self):
        self.app.local.navigate(self.root / "문서")
        self.app.local.cursor = 0
        self.app._on_key("F8")
        self.assertIsNone(self.app.modal)
        self.assertIn("지울 항목을 고르세요", self.app.status)

    def test_symlink_is_unlinked_not_followed(self):
        import os

        outside = self.root / "문서" / "실제.txt"
        outside.write_bytes(b"keep me")
        link = self.root / "링크.txt"
        os.symlink(outside, link)

        fsops.remove_tree(link)
        self.assertFalse(link.exists())
        self.assertTrue(outside.exists(), "심링크만 지워야 하고 대상은 남아야 한다")


class TestRemoteOperations(unittest.TestCase):
    """원격 mkdir/delete/head 를 socketpair 로 맞물린 세션에서 확인한다."""

    def setUp(self):
        import socket

        self.tmp_a = tempfile.TemporaryDirectory()
        self.tmp_b = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_a.cleanup)
        self.addCleanup(self.tmp_b.cleanup)
        self.root_a = Path(self.tmp_a.name).resolve()
        self.root_b = Path(self.tmp_b.name).resolve()

        self.shares_a = make_shares(self.root_a)
        self.shares_b = make_shares(self.root_b)
        self.rb = only_name(self.shares_b)

        sock_a, sock_b = socket.socketpair()
        self.a = session.Session(sock_a, "b", "127.0.0.1", self.shares_a, queue.Queue())
        self.b = session.Session(sock_b, "a", "127.0.0.1", self.shares_b, queue.Queue())
        self.a.start()
        self.b.start()
        self.addCleanup(self.b.close)
        self.addCleanup(self.a.close)

    def test_remote_mkdir(self):
        self.a.remote_mkdir(rpath(self.rb, "새폴더"))
        self.assertTrue((self.root_b / "새폴더").is_dir())

    def test_remote_mkdir_outside_root_is_refused(self):
        with self.assertRaises(session.SessionError):
            self.a.remote_mkdir("/tmp/mdirnet-nope")

    def test_remote_mkdir_duplicate_is_refused(self):
        (self.root_b / "있음").mkdir()
        with self.assertRaises(session.SessionError):
            self.a.remote_mkdir(rpath(self.rb, "있음"))

    def test_remote_delete_file(self):
        target = self.root_b / "지울것.txt"
        target.write_bytes(b"bye")
        self.a.remote_delete(rpath(self.rb, "지울것.txt"))
        self.assertFalse(target.exists())

    def test_remote_delete_directory_recursively(self):
        base = self.root_b / "폴더"
        (base / "안쪽").mkdir(parents=True)
        (base / "안쪽" / "f.txt").write_bytes(b"x")
        self.a.remote_delete(rpath(self.rb, "폴더"))
        self.assertFalse(base.exists())

    def test_remote_delete_outside_root_is_refused(self):
        with self.assertRaises(session.SessionError):
            self.a.remote_delete("/etc/hosts")

    def test_remote_delete_cannot_remove_the_share_root(self):
        """공유 폴더 자체를 지우면 세션이 가리키는 바닥이 사라진다."""
        with self.assertRaises(session.SessionError):
            self.a.remote_delete(self.rb)
        self.assertTrue(self.root_b.is_dir())

    def test_remote_delete_missing_item_is_refused(self):
        with self.assertRaises(session.SessionError):
            self.a.remote_delete(rpath(self.rb, "없음"))

    def test_remote_head_returns_prefix_and_total_size(self):
        target = self.root_b / "big.txt"
        target.write_bytes(b"0123456789" * 1000)

        data, size = self.a.remote_head(rpath(self.rb, "big.txt"), 50)
        self.assertEqual(len(data), 50)
        self.assertEqual(data[:10], b"0123456789")
        self.assertEqual(size, 10000)

    def test_remote_head_of_small_file_returns_all(self):
        target = self.root_b / "small.txt"
        target.write_bytes("한 줄\n".encode("utf-8"))
        data, size = self.a.remote_head(rpath(self.rb, "small.txt"), 4096)
        self.assertEqual(data.decode("utf-8"), "한 줄\n")
        self.assertEqual(size, len("한 줄\n".encode("utf-8")))

    def test_remote_head_outside_root_is_refused(self):
        with self.assertRaises(session.SessionError):
            self.a.remote_head("/etc/hosts", 100)

    def test_remote_head_of_directory_is_refused(self):
        (self.root_b / "dir").mkdir()
        with self.assertRaises(session.SessionError):
            self.a.remote_head(rpath(self.rb, "dir"), 100)

    def test_remote_head_respects_the_server_side_cap(self):
        """상대가 터무니없이 큰 limit 을 보내도 서버가 상한을 지킨다."""
        from mdirnet import config

        target = self.root_b / "huge.bin"
        target.write_bytes(b"z" * (config.MAX_VIEW_BYTES + 5000))

        data, _ = self.a.remote_head(rpath(self.rb, "huge.bin"), config.MAX_VIEW_BYTES * 10)
        self.assertEqual(len(data), config.MAX_VIEW_BYTES)

    def test_remote_head_refuses_a_negative_limit(self):
        """음수 limit 은 read(-1) 이 되어 파일을 통째로 읽어버린다."""
        from mdirnet import config

        target = self.root_b / "big.txt"
        target.write_bytes(b"0123456789" * 500)

        for limit in (-1, -config.MAX_VIEW_BYTES):
            data, size = self.a.remote_head(rpath(self.rb, "big.txt"), limit)
            self.assertEqual(data, b"", limit)
            self.assertEqual(size, 5000, limit)


if __name__ == "__main__":
    unittest.main()


class TestFolderNameTakesTheExtensionColumn(MdirTestCase):
    """폴더는 확장자가 없으니 그 칸까지 이름에 내준다."""

    INNER = 62

    def _row(self, name):
        rows = self.app.local.rows()
        index = next(i for i, e in enumerate(rows) if e["name"] == name)
        line = self.app._entry_line(self.app.local, rows[index], index, self.INNER, False)
        return screen._ANSI.sub("", line)

    @staticmethod
    def _ends_at(row, needle):
        """needle 이 끝나는 표시 칸. 한글이 섞이므로 글자 수로는 못 센다."""
        cut = row.index(needle) + len(needle)
        return screen.width(row[:cut])

    @staticmethod
    def _starts_at(row, needle):
        return screen.width(row[:row.index(needle)])

    def test_folder_name_room_is_wider_by_the_extension_column(self):
        """이름 칸이 확장자 칸만큼 넓어진다. 구분 한 칸은 그대로 남는다.

        이름 길이를 눈대중으로 정하면 폭이 넉넉할 때 아무것도 증명하지 못한다.
        실제 자리를 재서 '파일 칸에는 안 들어가고 폴더 칸에는 들어가는' 길이를
        만들어 쓴다.
        """
        size_start = self._starts_at(self._row("문서"), "[ SubDir ]")
        folder_room = size_start - 2  # 맨 앞 선택 표시 한 칸, 뒤에 구분 한 칸
        file_room = size_start - 1 - (app_module.EXT_COL + 1)
        self.assertEqual(folder_room - file_room, app_module.EXT_COL)

        name = "a" * folder_room
        (self.root / name).mkdir()
        self.app.local.refresh()

        self.assertGreater(screen.width(name), file_room, "전제가 깨졌다")
        self.assertIn(name, self._row(name), "폴더 이름이 온전히 나와야 한다")

    def test_the_name_never_touches_the_size_column(self):
        """[ SubDir ] 이 크기 칸을 꽉 채우므로 이름이 붙으면 읽기 나쁘다."""
        size_start = self._starts_at(self._row("문서"), "[ SubDir ]")
        for name in ("a" * (size_start - 2), "가" * 80, "docs", "my.folder"):
            path = self.root / name
            if not path.exists():
                path.mkdir()
            self.app.local.refresh()
            row = self._row(name)
            cut = row.index("[ SubDir ]")
            self.assertEqual(row[cut - 1], " ", "%r 앞에 빈칸이 없다: %r" % (name, row))

    def test_a_name_that_fills_the_room_leaves_exactly_one_space(self):
        """딱 맞게 채운 이름이면 한 칸이다.

        두 칸 문자가 잘릴 때는 반쪽을 남길 수 없어 빈칸이 두 개가 될 수 있다.
        그건 폭 계산상 어쩔 수 없으므로 여기서는 ASCII 로 딱 맞춰 확인한다.
        """
        size_start = self._starts_at(self._row("문서"), "[ SubDir ]")
        name = "a" * (size_start - 2)
        (self.root / name).mkdir()
        self.app.local.refresh()

        row = self._row(name)
        cut = row.index("[ SubDir ]")
        self.assertEqual(row[cut - 1], " ")
        self.assertNotEqual(row[cut - 2], " ", row)

    def test_the_gap_survives_truncation(self):
        name = "가" * 80
        (self.root / name).mkdir()
        self.app.local.refresh()
        row = self._row(name)
        cut = row.index("[ SubDir ]")
        self.assertIn("~", row[:cut], "잘렸으면 표시가 남아야 한다")
        self.assertEqual(row[cut - 1], " ", row)

    def test_a_name_too_long_for_even_the_wider_room_is_truncated(self):
        name = "나" * 80
        (self.root / name).mkdir()
        self.app.local.refresh()
        row = self._row(name)
        self.assertIn("~", row, "잘렸으면 표시가 남아야 한다")
        self.assertEqual(screen.width(row), self.INNER)

    def test_a_dotted_folder_name_is_not_split(self):
        (self.root / "my.folder").mkdir()
        self.app.local.refresh()
        row = self._row("my.folder")
        self.assertIn("my.folder", row)
        self.assertNotIn("FOLDER", row, "확장자로 떼어내면 안 된다")

    def test_the_size_column_stays_aligned_with_file_rows(self):
        folder = self._row("문서")
        a_file = self._row("a.txt")
        # 크기 칸은 오른쪽 정렬이므로 두 줄에서 같은 칸에서 끝나야 한다
        self.assertEqual(
            self._ends_at(folder, "[ SubDir ]"), self._ends_at(a_file, "100 B")
        )

    def test_the_up_row_gets_the_same_treatment(self):
        row = self._row("..")
        self.assertEqual(
            self._ends_at(row, "[ Up-Dir ]"), self._ends_at(self._row("a.txt"), "100 B")
        )

    def test_file_rows_still_show_the_extension_column(self):
        row = self._row("a.txt")
        self.assertIn("TXT", row)
        self.assertIn(" a ", row[:12], row)

    def test_extensionless_files_keep_the_column_for_now(self):
        """확장자 없는 파일은 폴더와 달리 지금 그대로 둔다 (요청 범위)."""
        (self.root / "Makefile").write_bytes(b"x")
        self.app.local.refresh()
        row = self._row("Makefile")
        folder = self._row("문서")
        self.assertLess(
            self._starts_at(row, "Makefile") + screen.width("Makefile") + app_module.EXT_COL,
            self._starts_at(folder, "[ SubDir ]"),
            "확장자 칸이 비어 있어야 한다",
        )
        # 크기 칸은 여전히 같은 자리에서 끝난다
        self.assertEqual(
            self._ends_at(row, "1 B"), self._ends_at(folder, "[ SubDir ]")
        )

    def test_every_row_keeps_the_exact_width(self):
        (self.root / "아주아주아주긴폴더이름입니다").mkdir()
        (self.root / "my.folder").mkdir()
        self.app.local.refresh()
        for inner in (30, 40, 62, 80, 120):
            for index, entry in enumerate(self.app.local.rows()):
                line = self.app._entry_line(self.app.local, entry, index, inner, False)
                self.assertEqual(
                    screen.width(screen._ANSI.sub("", line)), inner,
                    "inner=%d %s" % (inner, entry["name"]),
                )
