# 한글 입력 상태에서도 단축키와 다이얼로그가 동작하는지 확인하는 테스트

import tempfile
import unittest
from pathlib import Path

from mdirnet import fsops, session
from mdirnet.ui import app as app_module
from mdirnet.ui import term

from .helpers import make_shares, only_name, rpath




class TestCommandKeyNormalisation(unittest.TestCase):
    """두벌식 자판에서 한글 입력이 켜져 있으면 y 대신 'ㅛ' 가 들어온다."""

    def test_maps_hangul_jamo_back_to_letters(self):
        pairs = {
            "ㅛ": "y",
            "ㅜ": "n",
            "ㅂ": "q",
            "ㅊ": "c",
            "ㅇ": "d",
            "ㄱ": "r",
            "ㄴ": "s",
            "ㅍ": "v",
            "ㅐ": "o",
            "ㅡ": "m",
            "ㅏ": "k",
            "ㅓ": "j",
            "ㅗ": "h",
        }
        for jamo, letter in pairs.items():
            self.assertEqual(term.command_key(jamo), letter, jamo)

    def test_leaves_plain_letters_alone(self):
        for key in ("y", "n", "q", "Z", "1", "+", "-", "?", "\\"):
            self.assertEqual(term.command_key(key), key)

    def test_leaves_named_keys_alone(self):
        for key in (term.ENTER, term.ESC, term.UP, term.INSERT, "F5", "CTRL-D"):
            self.assertEqual(term.command_key(key), key)


class ApprovalTestCase(unittest.TestCase):
    def make(self):
        request = session.ApprovalRequest("other-pc", "192.168.0.9", "windows")
        return request, app_module.ApprovalDialog(request)


class TestApprovalDialog(ApprovalTestCase):
    def test_y_accepts_and_n_rejects(self):
        for key, expected in (("y", True), ("Y", True), ("n", False), ("N", False)):
            request, dialog = self.make()
            self.assertTrue(dialog.key(key))
            self.assertEqual(request.wait(0.1), expected, key)

    def test_esc_rejects(self):
        request, dialog = self.make()
        self.assertTrue(dialog.key(term.ESC))
        self.assertFalse(request.wait(0.1))

    def test_arrow_keys_move_the_cursor_without_deciding(self):
        request, dialog = self.make()
        self.assertTrue(dialog.accept)

        self.assertFalse(dialog.key(term.RIGHT))
        self.assertFalse(dialog.accept)
        self.assertFalse(request._decided.is_set(), "고르기만 했는데 결정되면 안 된다")

        self.assertFalse(dialog.key(term.LEFT))
        self.assertTrue(dialog.accept)

    def test_tab_and_space_also_move(self):
        for key in (term.TAB, term.SPACE):
            request, dialog = self.make()
            dialog.key(key)
            self.assertFalse(dialog.accept, key)

    def test_enter_confirms_the_highlighted_button(self):
        request, dialog = self.make()
        self.assertTrue(dialog.key(term.ENTER))  # 기본은 수락
        self.assertTrue(request.wait(0.1))

        request, dialog = self.make()
        dialog.key(term.RIGHT)  # 거부로 옮김
        self.assertTrue(dialog.key(term.ENTER))
        self.assertFalse(request.wait(0.1))

    def test_highlight_follows_the_cursor(self):
        _, dialog = self.make()
        button_line = [l for l in dialog.lines(60) if "수락" in l][0]
        # 처음에는 '수락' 쪽이 반전되어 있어야 한다
        self.assertIn(term.REVERSE, button_line.split("수락")[0])

        dialog.key(term.RIGHT)
        moved = [l for l in dialog.lines(60) if "수락" in l][0]
        self.assertNotIn(term.REVERSE, moved.split("수락")[0])
        self.assertIn(term.REVERSE, moved.split("거부")[0].rsplit("수락", 1)[-1])

    def test_footer_explains_the_keys(self):
        _, dialog = self.make()
        rendered = "\n".join(dialog.lines(60))
        self.assertIn("Enter 확정", rendered)
        self.assertIn("< >", rendered)


class TestApprovalThroughTheApp(unittest.TestCase):
    """앱을 거쳐 들어올 때 한글 입력 상태에서도 반응해야 한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.app = app_module.App(
            my_name="ime-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)

    def incoming(self):
        request = session.ApprovalRequest("peer", "10.0.0.5", "windows")
        self.app.events.put(("approval", request))
        self.app._drain_events()
        return request

    def test_hangul_y_accepts(self):
        """이 테스트가 사용자가 겪은 문제를 직접 재현한다."""
        request = self.incoming()
        self.assertIsInstance(self.app.modal, app_module.ApprovalDialog)

        self.app._on_key("ㅛ")  # 한글 입력 상태로 y 를 누른 결과
        self.assertIsNone(self.app.modal, "다이얼로그가 닫혀야 한다")
        self.assertTrue(request.wait(0.1))

    def test_hangul_n_rejects(self):
        request = self.incoming()
        self.app._on_key("ㅜ")
        self.assertIsNone(self.app.modal)
        self.assertFalse(request.wait(0.1))

    def test_arrow_then_enter_works_regardless_of_input_mode(self):
        request = self.incoming()
        self.app._on_key(term.RIGHT)
        self.assertIsNotNone(self.app.modal)
        self.app._on_key(term.ENTER)
        self.assertFalse(request.wait(0.1))

    def test_hangul_q_still_quits_the_app(self):
        self.app._on_key("ㅂ")
        self.assertFalse(self.app.running)

    def test_text_input_keeps_hangul_as_typed(self):
        """입력창에서는 되돌리면 안 된다. 한글 폴더 이름을 적어야 하니까."""
        self.app._on_key("F7")
        self.assertIsInstance(self.app.modal, app_module.InputDialog)

        for ch in "새폴더":
            self.app._on_key(ch)
        self.assertEqual(self.app.modal.text, "새폴더")

        self.app._on_key(term.ENTER)
        self.assertTrue((self.root / "새폴더").is_dir())

    def test_mask_input_keeps_hangul(self):
        self.app._on_key("+")
        self.app.modal.text = ""
        for ch in "보고서":
            self.app._on_key(ch)
        self.assertEqual(self.app.modal.text, "보고서")


class TestConfirmDialog(unittest.TestCase):
    def make(self):
        got = []
        return got, app_module.ConfirmDialog("t", ["몸통"], got.append)

    def test_cursor_starts_on_cancel(self):
        """되돌릴 수 없는 작업이 있으므로 기본값은 취소여야 한다."""
        _, dialog = self.make()
        self.assertFalse(dialog.proceed)

    def test_enter_without_moving_cancels(self):
        got, dialog = self.make()
        self.assertTrue(dialog.key(term.ENTER))
        self.assertEqual(got, [False])

    def test_arrow_then_enter_proceeds(self):
        got, dialog = self.make()
        dialog.key(term.LEFT)
        self.assertTrue(dialog.key(term.ENTER))
        self.assertEqual(got, [True])

    def test_y_still_proceeds_directly(self):
        got, dialog = self.make()
        self.assertTrue(dialog.key("y"))
        self.assertEqual(got, [True])

    def test_esc_cancels(self):
        got, dialog = self.make()
        self.assertTrue(dialog.key(term.ESC))
        self.assertEqual(got, [False])


class TestHangulThroughDeleteFlow(unittest.TestCase):
    """한글 입력 상태에서 삭제 확인까지 통과하는지 본다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "지울것.txt").write_bytes(b"x")
        self.app = app_module.App(
            my_name="n",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.refresh()

    def test_hangul_shortcuts_reach_the_confirm_and_accept_it(self):
        self.app.local.cursor = next(
            i for i, e in enumerate(self.app.local.rows()) if e["name"] == "지울것.txt"
        )
        self.app._on_key("F8")
        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)

        self.app._on_key("ㅛ")  # 한글 상태의 y
        self.assertFalse((self.root / "지울것.txt").exists())

    def test_hangul_s_cycles_sort(self):
        self.app._on_key("ㄴ")  # 한글 상태의 s
        self.assertIn("확장자", self.app.status)


if __name__ == "__main__":
    unittest.main()
