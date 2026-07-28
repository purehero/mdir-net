# 메뉴에 적힌 키 힌트가 실제로 그 동작을 부르는지 확인하는 테스트
#
# 하단 바를 재구성하면서 Commands 메뉴의 힌트가 옛 키(2, 6)로 남아 있었다.
# 화면에 적힌 키를 눌렀는데 다른 일이 벌어지는 것은 그냥 거짓말이다.
# 이 검사가 그런 어긋남을 잡는다.

import tempfile
import unittest
from pathlib import Path

from mdirnet.ui import app as app_module
from mdirnet.ui import term

from .helpers import make_shares


class HintTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "sub").mkdir()
        (self.root / "note.txt").write_bytes(b"hello")

    def fresh_app(self):
        app = app_module.App(
            my_name="hint-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(app.shutdown)
        app.local.refresh()
        app.local.cursor = 1  # '..' 이 아닌 항목에 커서를 둔다
        return app

    def outcome(self, app):
        """동작의 결과를 비교 가능한 형태로 요약한다."""
        return (type(app.modal).__name__, app.status, app.running, app.show_hidden)


class TestEveryHintMatchesItsAction(HintTestCase):
    def test_pressing_the_hint_does_the_same_as_choosing_the_item(self):
        reference = self.fresh_app()
        menus = app_module.MenuBar(reference).menus

        checked = 0
        for title, items in menus:
            for label, hint, action in items:
                if action is None or not hint:
                    continue

                # (1) 메뉴 항목을 직접 실행한 결과
                by_menu_app = self.fresh_app()
                by_menu = app_module.MenuBar(by_menu_app)
                target = next(
                    a for t, its in by_menu.menus if t == title
                    for l, h, a in its if l == label
                )
                target()
                expected = self.outcome(by_menu_app)

                # (2) 화면에 적힌 키를 누른 결과
                by_key_app = self.fresh_app()
                by_key_app._on_key(hint)
                actual = self.outcome(by_key_app)

                self.assertEqual(
                    actual,
                    expected,
                    "%s 메뉴의 '%s' 는 힌트가 '%s' 인데 그 키가 다른 일을 합니다"
                    % (title, label, hint),
                )
                checked += 1

        self.assertGreater(checked, 8, "검사한 항목이 너무 적습니다")

    def test_connect_hint_is_n_not_a_digit(self):
        """연결은 하단 바에서 빠졌으므로 숫자 힌트를 달면 안 된다."""
        app = self.fresh_app()
        for title, items in app_module.MenuBar(app).menus:
            for label, hint, action in items:
                if label == "연결할 PC 찾기":
                    self.assertEqual(hint, "n")
                if label == "연결 끊기":
                    self.assertEqual(hint, "d")

    def test_no_hint_points_at_an_unbound_key(self):
        app = self.fresh_app()
        bound = {n for n, _ in app_module._FKEYS}
        bound |= {"F%d" % i for i in range(1, 11)}
        bound |= {"n", "d", "r", "s", "v", "o", "c", "m", "q", "?", "/", ".", "=", "+", "-"}

        for title, items in app_module.MenuBar(app).menus:
            for label, hint, action in items:
                if action is None or not hint:
                    continue
                self.assertIn(hint, bound, "%s / %s 의 힌트 %r" % (title, label, hint))

    def test_sort_items_have_no_hint_because_no_single_key_sets_a_mode(self):
        """`2`/`s` 는 순환이라 특정 기준을 고르는 키가 아니다. 빈 힌트가 맞다."""
        app = self.fresh_app()
        for title, items in app_module.MenuBar(app).menus:
            for label, hint, action in items:
                if "정렬" in label and action is not None:
                    self.assertEqual(hint, "", label)


class TestRightPaneSource(HintTestCase):
    def test_right_starts_as_remote(self):
        app = self.fresh_app()
        self.assertTrue(app.right.is_remote)
        self.assertIs(app.right, app.remote)
        self.assertIs(app.left, app.local)

    def test_equals_switches_the_right_pane_to_local(self):
        app = self.fresh_app()
        app._on_key("=")

        self.assertFalse(app.right.is_remote)
        self.assertEqual(app.right.label, "내 PC")
        self.assertIn("내 PC", app.status)

    def test_right_pane_in_local_mode_lists_files(self):
        app = self.fresh_app()
        app._on_key("=")
        app.right.navigate(self.root)

        self.assertIn("note.txt", [e["name"] for e in app.right.entries])

    def test_switching_back_needs_a_session(self):
        app = self.fresh_app()
        app._on_key("=")
        app._on_key("=")  # 연결이 없으면 상대로 못 돌아간다

        self.assertFalse(app.right.is_remote)
        self.assertIn("연결된 상대가 없습니다", app.status)

    def test_switching_back_works_with_a_session(self):
        app = self.fresh_app()
        app._on_key("=")
        app.session = _StubSession()
        app._on_key("=")
        self.assertTrue(app.right.is_remote)

    def test_left_pane_is_never_switchable(self):
        """왼쪽은 늘 내 PC 다. 대상을 바꾸는 항목이 Left 메뉴에 없어야 한다."""
        app = self.fresh_app()
        for title, items in app_module.MenuBar(app).menus:
            if title == "Left":
                labels = [l for l, _, a in items if a]
                self.assertNotIn("상대 보기", labels)
                self.assertNotIn("내 PC 보기", labels)

    def test_right_menu_label_reflects_state(self):
        app = self.fresh_app()
        right = next(items for t, items in app_module.MenuBar(app).menus if t == "Right")
        self.assertEqual(right[0][0], "내 PC 보기")

        app._on_key("=")
        right = next(items for t, items in app_module.MenuBar(app).menus if t == "Right")
        self.assertEqual(right[0][0], "상대 보기")

    def test_right_menu_offers_drives_only_in_local_mode(self):
        app = self.fresh_app()
        labels = [l for t, its in app_module.MenuBar(app).menus if t == "Right" for l, _, a in its if a]
        self.assertNotIn("드라이브 바꾸기", labels)

        app._on_key("=")
        labels = [l for t, its in app_module.MenuBar(app).menus if t == "Right" for l, _, a in its if a]
        self.assertIn("드라이브 바꾸기", labels)

    def test_tab_reaches_the_right_pane_without_a_session(self):
        app = self.fresh_app()
        app._on_key("=")
        app._on_key(term.TAB)
        self.assertIs(app.focus, app.right)

    def test_disconnect_does_not_wipe_a_local_right_pane(self):
        app = self.fresh_app()
        app.session = _StubSession()
        app._on_key("=")  # 내 PC 로
        app.right.navigate(self.root)
        self.assertTrue(app.right.entries)

        app.events.put(("disconnected", "끊김"))
        app._drain_events()
        self.assertTrue(app.right.entries, "내 PC 목록이 지워졌다")

    def test_connecting_turns_the_right_pane_back_to_remote(self):
        app = self.fresh_app()
        app._on_key("=")
        app.events.put(("connected", _StubSession()))
        app._drain_events()
        self.assertTrue(app.right.is_remote)


class TestLocalToLocalCopy(HintTestCase):
    def setUp(self):
        super().setUp()
        self.dest = self.root / "복사대상"
        self.dest.mkdir()
        (self.root / "big.bin").write_bytes(bytes(range(256)) * 400)

    def prepare(self):
        app = self.fresh_app()
        app._on_key("=")  # 오른쪽을 내 PC 로
        app.right.navigate(self.dest)
        app.left.navigate(self.root)
        return app

    def test_copy_between_two_local_panes_needs_no_session(self):
        app = self.prepare()
        app.left.cursor = next(
            i for i, e in enumerate(app.left.rows()) if e["name"] == "note.txt"
        )

        app._on_key("5")
        self.assertIsInstance(app.modal, app_module.ConfirmDialog)
        app._on_key("y")

        import time

        for _ in range(200):
            app._drain_events()
            if (self.dest / "note.txt").is_file():
                break
            time.sleep(0.02)

        self.assertEqual((self.dest / "note.txt").read_bytes(), b"hello")

    def test_copying_a_large_file_locally(self):
        app = self.prepare()
        app.left.cursor = next(
            i for i, e in enumerate(app.left.rows()) if e["name"] == "big.bin"
        )
        app._on_key("5")
        app._on_key("y")

        import hashlib
        import time

        for _ in range(300):
            app._drain_events()
            if (self.dest / "big.bin").is_file() and app.transfer is None:
                break
            time.sleep(0.02)

        source = hashlib.sha256((self.root / "big.bin").read_bytes()).hexdigest()
        copied = hashlib.sha256((self.dest / "big.bin").read_bytes()).hexdigest()
        self.assertEqual(source, copied)

    def test_no_part_file_left_behind(self):
        app = self.prepare()
        app.left.cursor = next(
            i for i, e in enumerate(app.left.rows()) if e["name"] == "note.txt"
        )
        app._on_key("5")
        app._on_key("y")

        import time

        for _ in range(200):
            app._drain_events()
            if (self.dest / "note.txt").is_file():
                break
            time.sleep(0.02)
        self.assertEqual([p.name for p in self.dest.glob("*.part")], [])

    def test_same_pane_is_refused(self):
        app = self.fresh_app()
        app.focus = app.left
        app.right.is_remote = False
        # 억지로 같은 패널을 원본과 대상으로 만든 상황을 흉내낸다
        app.right = app.left
        app._on_key("5")
        self.assertIsNone(app.modal)
        self.assertIn("같은 패널", app.status)


class _StubSession:
    peer_name = "stub-pc"
    peer_host = "127.0.0.1"

    def close(self, reason=None):
        pass

    def remote_home(self):
        return "."


if __name__ == "__main__":
    unittest.main()
