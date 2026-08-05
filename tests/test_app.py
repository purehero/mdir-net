# 터미널 없이 App 을 세워 화면 구성과 키 처리를 확인하는 테스트

import queue
import tempfile
import unittest
from pathlib import Path

from mdirnet import session
from mdirnet.ui import app as app_module
from mdirnet.ui import screen, term

from .helpers import make_shares, only_name, rpath




class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

        (self.root / "문서").mkdir()
        (self.root / "보고서.txt").write_bytes(b"x" * 2048)
        (self.root / "photo.jpg").write_bytes(b"y" * 500)

        # 포트 0 으로 커널에 맡기고, 발견 기능은 꺼서 브로드캐스트를 쏘지 않는다
        self.app = app_module.App(
            my_name="test-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.refresh()

    def compose(self, size=(100, 30)):
        return self.app._compose(size), size


class TestLayout(AppTestCase):
    def test_every_line_is_exactly_terminal_width(self):
        """한 칸이라도 넘치면 줄바꿈이 생겨 화면 전체가 밀린다."""
        for size in ((80, 24), (100, 30), (40, 10), (200, 60)):
            lines, _ = self.compose(size)
            for index, line in enumerate(lines):
                self.assertEqual(
                    screen.width(line), size[0], "size=%s line %d: %r" % (size, index, line)
                )

    def test_line_count_matches_terminal_height(self):
        for size in ((80, 24), (40, 10), (120, 50)):
            lines, _ = self.compose(size)
            self.assertEqual(len(lines), size[1], "size=%s" % (size,))

    def test_local_entries_are_listed(self):
        lines, _ = self.compose()
        body = "\n".join(lines)
        self.assertIn("문서", body)
        # MDir 처럼 이름과 확장자가 나뉘고 크기는 천 단위로 끊는다
        self.assertIn("보고서", body)
        self.assertIn("TXT", body)
        self.assertIn("2.0 KB", body)
        self.assertIn("[ SubDir ]", body)

    def test_disconnected_pane_invites_connection(self):
        self.assertIn("연결되지 않음", "\n".join(self.compose()[0]))

    def test_hangul_filenames_keep_the_border_aligned(self):
        (self.root / "아주아주긴한글파일이름입니다.bin").write_bytes(b"z")
        self.app.local.refresh()

        lines, size = self.compose((80, 24))
        for line in lines:
            self.assertEqual(screen.width(line), size[0])


class TestNavigation(AppTestCase):
    def test_cursor_moves_and_clamps(self):
        self.app._on_key(term.DOWN)
        self.assertEqual(self.app.local.cursor, 1)

        for _ in range(50):
            self.app._on_key(term.DOWN)
        self.assertEqual(self.app.local.cursor, len(self.app.local.rows()) - 1)

        for _ in range(50):
            self.app._on_key(term.UP)
        self.assertEqual(self.app.local.cursor, 0)

    def test_enter_descends_into_directory(self):
        rows = self.app.local.rows()
        index = next(i for i, e in enumerate(rows) if e["name"] == "문서")
        self.app.local.cursor = index

        self.app._on_key(term.ENTER)
        self.assertEqual(self.app.local.path, self.root / "문서")

    def test_dotdot_goes_up(self):
        self.app.local.navigate(self.root / "문서")
        self.app.local.cursor = 0  # '..'
        self.assertTrue(self.app.local.current_item()["up"])

        self.app._on_key(term.ENTER)
        self.assertEqual(self.app.local.path, self.root)

    def test_space_selects_and_advances(self):
        rows = self.app.local.rows()
        index = next(i for i, e in enumerate(rows) if e["name"] == "photo.jpg")
        self.app.local.cursor = index

        self.app._on_key(term.SPACE)
        self.assertIn("photo.jpg", self.app.local.selected)
        self.assertEqual(self.app.local.cursor, index + 1)

        self.app.local.cursor = index
        self.app._on_key(term.SPACE)
        self.assertNotIn("photo.jpg", self.app.local.selected)

    def test_dotdot_is_never_selectable(self):
        self.app.local.navigate(self.root / "문서")
        self.app.local.cursor = 0
        self.app._on_key(term.SPACE)
        self.assertEqual(self.app.local.selected, set())

    def test_tab_does_not_focus_remote_while_disconnected(self):
        self.app._on_key(term.TAB)
        self.assertIs(self.app.focus, self.app.local)

    def test_q_quits(self):
        self.app._on_key("q")
        self.assertFalse(self.app.running)


class TestCopyGuards(AppTestCase):
    def test_copy_without_connection_is_refused(self):
        self.app._on_key("F5")
        self.assertIsNone(self.app.modal)
        self.assertIn("연결", self.app.status)

    def test_copy_into_the_virtual_root_is_refused_with_guidance(self):
        """공유 목록 자체에는 파일을 둘 수 없다. 파일마다 오류를 내지 않고 미리 알린다."""
        self.app.session = _StubSession()
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == "photo.jpg"
        )

        self.app._on_key("F5")
        self.assertIsNone(self.app.modal)
        self.assertIn("공유 폴더 안으로", self.app.status)

    def test_copy_asks_for_confirmation_when_connected(self):
        self.app.session = _StubSession()
        # 가상 루트에는 파일을 둘 수 없으므로 공유 폴더 안으로 들어간 상태로 둔다
        self.app.remote.path = "상대공유"
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == "photo.jpg"
        )

        self.app._on_key("F5")
        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)
        # 겹치는 이름이 없으면 덮어쓰기 경고를 띄우지 않는다
        self.assertNotIn("덮어씁니다", "\n".join(self.app.modal.lines(60)))
        self.assertIn("photo.jpg", "\n".join(self.app.modal.lines(60)))

    def test_disconnect_without_session_reports_it(self):
        self.app._on_key("d")  # 끊기는 문자 키와 Commands 메뉴에만 있다
        self.assertIn("연결된 상대가 없습니다", self.app.status)


class TestDialogs(AppTestCase):
    def test_help_opens_and_any_key_closes(self):
        self.app._on_key("F1")
        self.assertIsInstance(self.app.modal, app_module.MessageDialog)

        self.app._on_key("x")
        self.assertIsNone(self.app.modal)

    def test_modal_overlay_keeps_line_widths(self):
        self.app._on_key("F1")
        lines, size = self.compose((80, 24))
        for line in lines:
            self.assertEqual(screen.width(line), size[0])

    def test_cut_box_is_marked(self):
        """줄일 수 없는 상자가 화면보다 크면 잘렸다는 표시를 남긴다.

        표시가 없으면 사용자는 상자 아래에 아무것도 없는 줄 안다.
        """
        self.app.modal = app_module.ConfirmDialog(
            "확인", ["줄 %d" % i for i in range(12)], lambda ok: None
        )
        self.assertIn("~~", self.app._compose((64, 12))[-1])
        self.assertNotIn("~~", "\n".join(self.app._compose((64, 30))))

    def test_incoming_approval_shows_dialog_and_yes_accepts(self):
        request = session.ApprovalRequest("other-pc", "192.168.0.9", "windows")
        self.app.events.put(("approval", request))
        self.app._drain_events()

        self.assertIsInstance(self.app.modal, app_module.ApprovalDialog)
        rendered = "\n".join(self.app.modal.lines(60))
        self.assertIn("other-pc", rendered)
        self.assertIn("192.168.0.9", rendered)

        self.app._on_key("y")
        self.assertIsNone(self.app.modal)
        self.assertTrue(request.wait(0.1))

    def test_approval_n_rejects(self):
        request = session.ApprovalRequest("other-pc", "10.0.0.2", "linux")
        self.app.events.put(("approval", request))
        self.app._drain_events()

        self.app._on_key("n")
        self.assertFalse(request.wait(0.1))

    def test_approval_is_auto_rejected_while_already_connected(self):
        self.app.session = _StubSession()
        request = session.ApprovalRequest("third-pc", "10.0.0.3", "linux")
        self.app.events.put(("approval", request))
        self.app._drain_events()

        self.assertIsNone(self.app.modal)
        self.assertFalse(request.wait(0.1))
        self.assertIn("거절", self.app.status)

    def test_peer_dialog_reports_empty_network(self):
        self.app._on_key("n")  # 연결은 문자 키와 Commands 메뉴에만 있다
        self.assertIsInstance(self.app.modal, app_module.PeerDialog)
        self.assertIn("찾지 못했습니다", "\n".join(self.app.modal.lines(60)))

    def test_progress_dialog_renders_ratio(self):
        transfer = app_module.Transfer("2개 항목", 1000)
        transfer.done = 250
        transfer.current = "sub/파일.bin"

        rendered = "\n".join(app_module.ProgressDialog(transfer).lines(50))
        self.assertIn("25%", rendered)
        self.assertIn("sub/파일.bin", rendered)

    def test_progress_ratio_never_exceeds_one(self):
        transfer = app_module.Transfer("x", 100)
        transfer.done = 9999
        self.assertEqual(transfer.ratio, 1.0)

    def test_progress_ratio_with_zero_total(self):
        self.assertEqual(app_module.Transfer("x", 0).ratio, 0.0)


class TestPlan(AppTestCase):
    def test_plan_expands_directories_recursively(self):
        deep = self.root / "문서" / "안쪽"
        deep.mkdir(parents=True)
        (self.root / "문서" / "a.txt").write_bytes(b"1")
        (deep / "b.txt").write_bytes(b"22")
        self.app.local.refresh()

        items = [e for e in self.app.local.entries if e["name"] == "문서"]
        plan, dirs = self.app._build_plan(self.app.local, items, None)

        self.assertEqual(
            {rel for rel, _, _ in plan},
            {"문서/a.txt", "문서/안쪽/b.txt"},
        )
        self.assertEqual(sum(size for _, _, size in plan), 3)
        # 폴더 자체와 하위 폴더도 계획에 들어간다 (빈 폴더 대비)
        self.assertEqual(dirs, ["문서", "문서/안쪽"])

    def test_plan_includes_an_empty_directory(self):
        """빈 폴더를 복사하면 아무 일도 안 나던 버그를 막는다."""
        (self.root / "빈폴더").mkdir()
        self.app.local.refresh()

        items = [e for e in self.app.local.entries if e["name"] == "빈폴더"]
        plan, dirs = self.app._build_plan(self.app.local, items, None)

        self.assertEqual(plan, [])
        self.assertEqual(dirs, ["빈폴더"])

    def test_plan_of_a_single_file(self):
        items = [e for e in self.app.local.entries if e["name"] == "photo.jpg"]
        plan, dirs = self.app._build_plan(self.app.local, items, None)
        self.assertEqual(plan, [("photo.jpg", self.root / "photo.jpg", 500)])
        self.assertEqual(dirs, [], "파일만 고르면 만들 폴더가 없다")

    def test_targets_prefers_selection_over_cursor(self):
        self.app.local.selected = {"photo.jpg", "보고서.txt"}
        self.app.local.cursor = 0
        names = {t["name"] for t in self.app.local.targets()}
        self.assertEqual(names, {"photo.jpg", "보고서.txt"})


class TestSessionLifecycle(AppTestCase):
    def test_disconnect_event_clears_remote_state(self):
        self.app.session = _StubSession()
        self.app.remote.entries = [{"name": "x", "is_dir": False, "size": 1, "mtime": 0}]
        self.app.focus = self.app.remote

        self.app.events.put(("disconnected", "상대가 종료했습니다"))
        self.app._drain_events()

        self.assertIsNone(self.app.session)
        self.assertEqual(self.app.remote.entries, [])
        self.assertIs(self.app.focus, self.app.local)
        self.assertIn("상대가 종료했습니다", self.app.status)

    def test_stale_listing_is_discarded(self):
        """디렉터리를 바꾼 뒤 도착한 낡은 응답이 화면을 되돌리면 안 된다."""
        pane = self.app.remote
        pane._generation = 5
        pane.apply_listing(3, "낡은공유", [{"name": "stale", "is_dir": False, "size": 0, "mtime": 0}])
        self.assertEqual(pane.entries, [])

        pane.apply_listing(5, "새공유/sub", [])
        self.assertEqual(pane.path, "새공유/sub")

    def test_copy_done_with_errors_opens_message(self):
        self.app.transfer = app_module.Transfer("x", 1)
        self.app.events.put(("copy_done", 2, ["a.txt — 권한 없음"]))
        self.app._drain_events()

        self.assertIsInstance(self.app.modal, app_module.MessageDialog)
        self.assertIsNone(self.app.transfer)

    def test_copy_done_clean_sets_status(self):
        self.app.transfer = app_module.Transfer("x", 1)
        self.app.events.put(("copy_done", 3, []))
        self.app._drain_events()

        self.assertIsNone(self.app.modal)
        self.assertIn("3개", self.app.status)


class TestFolderDialog(AppTestCase):
    def make(self, start=None, hint="Esc 취소"):
        self.chosen = []
        self.cancelled = []
        return app_module.FolderDialog(
            start or self.root,
            on_choose=self.chosen.append,
            on_cancel=lambda: self.cancelled.append(True),
            cancel_hint=hint,
        )

    def test_lists_only_directories(self):
        dialog = self.make()
        self.assertIn("문서", dialog.rows())
        self.assertNotIn("보고서.txt", dialog.rows())
        self.assertNotIn("photo.jpg", dialog.rows())

    def test_dotdot_is_offered_when_not_at_filesystem_root(self):
        self.assertEqual(self.make().rows()[0], "..")

    def test_enter_descends_and_left_returns(self):
        (self.root / "문서" / "안쪽").mkdir()
        dialog = self.make()
        dialog.cursor = dialog.rows().index("문서")

        dialog.key(term.ENTER)
        self.assertEqual(dialog.path, self.root / "문서")
        self.assertIn("안쪽", dialog.rows())

        dialog.key(term.LEFT)
        self.assertEqual(dialog.path, self.root)

    def test_enter_on_dotdot_goes_up(self):
        dialog = self.make(self.root / "문서")
        dialog.cursor = 0
        dialog.key(term.ENTER)
        self.assertEqual(dialog.path, self.root)

    def test_ctrl_d_chooses_current_folder_and_closes(self):
        dialog = self.make()
        self.assertTrue(dialog.key("CTRL-D"))
        self.assertEqual(self.chosen, [self.root])

    def test_space_and_insert_also_choose(self):
        """Ctrl 이 없는 자판에서도 결정할 수 있어야 한다."""
        for key in (term.SPACE, term.INSERT):
            dialog = self.make()
            self.assertTrue(dialog.key(key), key)
            self.assertEqual(self.chosen, [self.root], key)

    def test_ctrl_d_chooses_the_folder_you_navigated_into(self):
        dialog = self.make()
        dialog.cursor = dialog.rows().index("문서")
        dialog.key(term.ENTER)
        dialog.key("CTRL-D")
        self.assertEqual(self.chosen, [self.root / "문서"])

    def test_esc_cancels_without_choosing(self):
        dialog = self.make()
        self.assertTrue(dialog.key(term.ESC))
        self.assertEqual(self.chosen, [])
        self.assertEqual(self.cancelled, [True])

    def test_cursor_clamps_at_both_ends(self):
        dialog = self.make()
        for _ in range(50):
            dialog.key(term.DOWN)
        self.assertEqual(dialog.cursor, len(dialog.rows()) - 1)
        for _ in range(50):
            dialog.key(term.UP)
        self.assertEqual(dialog.cursor, 0)

    def test_enter_on_empty_listing_does_nothing(self):
        empty = self.root / "빈폴더"
        empty.mkdir()
        dialog = self.make(empty)
        dialog.cursor = 0
        dialog.key(term.ENTER)  # '..' 뿐이므로 상위로 올라간다
        self.assertEqual(dialog.path, self.root)

    def test_cancel_hint_is_shown(self):
        self.assertIn("Esc 종료", "\n".join(self.make(hint="Esc 종료").lines(60)))
        self.assertIn("Space 이 폴더로 결정", "\n".join(self.make().lines(60)))

    def test_confirm_hint_survives_a_short_screen(self):
        """상자가 화면보다 커지면 아래쪽이 잘려 결정하는 방법을 알 수 없다."""
        many = self.root / "많은폴더"
        many.mkdir()
        for i in range(30):
            (many / ("sub%02d" % i)).mkdir()

        self.app.modal = self.make(many)
        for rows in range(10, 31):
            drawn = "\n".join(self.app._compose((64, rows)))
            self.assertIn("Space 이 폴더로 결정", drawn, "rows=%d" % rows)

    def test_short_screen_keeps_the_cursor_row_visible(self):
        """안내줄을 살리려고 목록을 0줄로 만들어 버리면 고를 수가 없다.

        낮은 화면에서는 목록이 한 줄까지 줄어든다. 그래도 커서가 놓인 줄은 늘
        보여야 움직이며 고를 수 있다.
        """
        dialog = self.make()
        self.app.modal = dialog
        self.app._compose((64, 12))  # max_body 를 낮은 화면 값으로 채운다

        dialog.cursor = dialog.rows().index("문서")
        self.assertIn("문서", "\n".join(self.app._compose((64, 12))))

    def test_dialog_height_is_stable_between_folders(self):
        """폴더마다 상자 높이가 들썩이면 화면이 어지럽다."""
        (self.root / "빈폴더").mkdir()
        many = self.root / "많은폴더"
        many.mkdir()
        for i in range(30):
            (many / ("sub%02d" % i)).mkdir()

        heights = set()
        for start in (self.root, self.root / "빈폴더", many):
            heights.add(len(self.make(start).lines(60)))
        self.assertEqual(len(heights), 1, heights)

    def test_overlay_keeps_line_widths(self):
        self.app.modal = self.make()
        for size in ((80, 24), (100, 30), (40, 12), (64, 10)):
            for line in self.app._compose(size):
                self.assertEqual(screen.width(line), size[0])

    def test_box_fits_at_every_height(self):
        """어느 높이에서도 상자가 화면 안에 들어가야 잘리지 않는다."""
        self.app.modal = self.make()
        for rows in range(10, 31):
            drawn = "\n".join(self.app._compose((64, rows)))
            self.assertIn(app_module.BOX_BL, drawn, "rows=%d" % rows)


class TestShareSelection(unittest.TestCase):
    """공유 목록이 비었을 때의 시작 흐름."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "고른폴더").mkdir()

    def make(self, *paths):
        app = app_module.App(
            my_name="picker",
            shares=make_shares(*paths),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(app.shutdown)
        return app

    def test_empty_list_defers_service_startup(self):
        """폴더를 고르기 전에는 아무것도 공유하지 않아야 한다."""
        app = self.make()
        self.assertTrue(app.pick_root_at_start)
        self.assertFalse(app._services_started)

    def test_existing_list_starts_without_a_dialog(self):
        app = self.make(self.root)
        self.assertFalse(app.pick_root_at_start)
        app.start_services()
        self.assertTrue(app._services_started)
        self.assertIsNone(app.modal)

    def test_adding_a_share_registers_it(self):
        app = self.make()
        share = app.add_share(self.root / "고른폴더")

        self.assertEqual(share.name, "고른폴더")
        self.assertEqual(app.shares.names, ["고른폴더"])
        # Server 가 같은 목록 객체를 보므로 곧바로 반영된다
        self.assertIs(app.server.shares, app.shares)

    def test_removing_a_share(self):
        app = self.make(self.root, self.root / "고른폴더")
        self.assertEqual(len(app.shares), 2)

        app.remove_share("고른폴더")
        self.assertEqual(app.shares.names, [self.root.name])

    def test_start_services_is_idempotent(self):
        app = self.make(self.root)
        app.start_services()
        app.start_services()
        self.assertTrue(app._services_started)

    def test_f4_opens_the_share_manager_when_idle(self):
        app = self.make(self.root)
        app._on_key("F4")
        self.assertIsInstance(app.modal, app_module.ShareDialog)

    def test_f4_opens_while_connected_too(self):
        """세션이 목록 객체를 그대로 보므로 연결 중 변경도 곧바로 적용된다."""
        app = self.make(self.root)
        app.session = _StubSession()

        app._on_key("F4")
        self.assertIsInstance(app.modal, app_module.ShareDialog)
        self.assertIn("곧바로 적용", app.status)

    def test_local_pane_starts_at_the_first_share(self):
        app = self.make(self.root)
        self.assertEqual(app.local.path, self.root)


class _StubSession:
    """연결된 것처럼 보이게 하는 최소 대역. 네트워크는 건드리지 않는다."""

    peer_name = "stub-pc"
    peer_host = "127.0.0.1"

    def close(self, reason=None):
        pass


if __name__ == "__main__":
    unittest.main()
