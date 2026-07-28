# 상단 숫자열을 기능키로 쓰는 기능 테스트
#
# 숫자를 고른 이유. macOS 는 F 키에 fn 이 필요하고, F 키의 이스케이프 시퀀스는
# 터미널마다 달라 놓치는 변형이 있다. 숫자는 어느 OS·터미널에서나 ASCII 한
# 바이트로 도착하고 한글 입력 상태에도 영향받지 않는다.

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mdirnet import fsops
from mdirnet.ui import app as app_module
from mdirnet.ui import term

from .helpers import make_shares, only_name, rpath




class DigitTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "폴더").mkdir()
        (self.root / "메모.txt").write_bytes("한 줄\n".encode("utf-8"))

        self.app = app_module.App(
            my_name="digit-node",
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


class TestMappingTable(unittest.TestCase):
    def test_every_digit_is_mapped(self):
        self.assertEqual(
            app_module._DIGIT_FKEYS,
            {
                "1": "F1",
                "2": "F2",
                "3": "F3",
                "4": "F4",
                "5": "F5",
                "6": "F6",
                "7": "F7",
                "8": "F8",
                "9": "F9",
                "0": "F10",
            },
        )

    def test_bar_labels_and_key_map_agree(self):
        """하단 바에 적힌 숫자가 실제로 그 기능을 부르는 숫자여야 한다."""
        labelled = [number for number, _ in app_module._FKEYS]
        self.assertEqual(labelled, ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"])
        # 바에 적힌 숫자가 곧 눌러야 하는 키여야 한다. '10' 이라고 적어두면
        # 키보드에 없는 키를 안내하는 셈이 된다
        for number in labelled:
            self.assertIn(number, app_module._DIGIT_FKEYS)
        self.assertEqual(app_module._DIGIT_FKEYS["0"], "F10")


class TestDigitsCallTheSameActions(DigitTestCase):
    def test_1_opens_help(self):
        self.app._on_key("1")
        self.assertIsInstance(self.app.modal, app_module.MessageDialog)
        self.assertEqual(self.app.modal.title, "도움말")

    def test_2_cycles_sort(self):
        """NC 배치에 맞춰 2번은 정렬이다. 연결은 n 과 Commands 메뉴로 옮겼다."""
        self.app._on_key("2")
        self.assertIn("확장자", self.app.status)

    def test_3_opens_viewer(self):
        self.cursor_to("메모.txt")
        self.app._on_key("3")
        self.assertIsInstance(self.app.modal, app_module.ViewerDialog)

    def test_4_opens_share_manager(self):
        self.app._on_key("4")
        self.assertIsInstance(self.app.modal, app_module.ShareDialog)

    def test_5_starts_copy_flow(self):
        self.app.session = _StubSession()
        self.app.remote.path = "상대공유"  # 가상 루트가 아니어야 복사가 시작된다
        self.cursor_to("메모.txt")
        self.app._on_key("5")
        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)

    def test_6_opens_rename(self):
        """NC 의 6번은 Rename/Move 다. 끊기는 d 와 Commands 메뉴로 옮겼다."""
        self.cursor_to("메모.txt")
        self.app._on_key("6")
        self.assertIsInstance(self.app.modal, app_module.InputDialog)
        self.assertEqual(self.app.modal.title, "이름 바꾸기")

    def test_7_opens_mkdir(self):
        self.app._on_key("7")
        self.assertIsInstance(self.app.modal, app_module.InputDialog)
        self.assertEqual(self.app.modal.title, "폴더 만들기")

    def test_8_asks_before_deleting(self):
        self.cursor_to("메모.txt")
        self.app._on_key("8")
        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)
        self.assertIn("되돌릴 수 없습니다", "\n".join(self.app.modal.lines(60)))

    def test_9_opens_the_menu_bar(self):
        """NC 처럼 F9(=9)는 상단 메뉴를 연다. 정렬은 s 와 메뉴 안에 있다."""
        self.app._on_key("9")
        self.assertIsInstance(self.app.modal, app_module.MenuBar)

    def test_0_quits(self):
        self.app._on_key("0")
        self.assertFalse(self.app.running)

    def test_digit_and_fkey_reach_the_same_place(self):
        for digit, fkey in (("4", "F4"), ("7", "F7")):
            by_digit = self._modal_after(digit)
            by_fkey = self._modal_after(fkey)
            self.assertEqual(type(by_digit), type(by_fkey), digit)

    def _modal_after(self, key):
        self.app.modal = None
        self.app._on_key(key)
        return self.app.modal


class TestDigitsDoNotBreakTextInput(DigitTestCase):
    def test_digits_are_typed_into_a_folder_name(self):
        self.app._on_key("7")
        for ch in "2024백업":
            self.app._on_key(ch)
        self.assertEqual(self.app.modal.text, "2024백업")

        self.app._on_key(term.ENTER)
        self.assertTrue((self.root / "2024백업").is_dir())

    def test_digits_are_typed_into_a_mask(self):
        (self.root / "노래.mp3").write_bytes(b"m")
        self.app.local.refresh()

        self.app._on_key("+")
        self.app.modal.text = "*."
        for ch in "mp3":
            self.app._on_key(ch)
        self.assertEqual(self.app.modal.text, "*.mp3")

        self.app._on_key(term.ENTER)
        self.assertEqual(self.app.local.selected, {"노래.mp3"})

    def test_a_digit_only_folder_name_works(self):
        self.app._on_key("7")
        for ch in "2025":
            self.app._on_key(ch)
        self.app._on_key(term.ENTER)
        self.assertTrue((self.root / "2025").is_dir())

    def test_zero_does_not_quit_while_typing(self):
        """입력창에서 0 이 종료로 새면 이름에 0 을 못 넣는다."""
        self.app._on_key("7")
        self.app._on_key("0")
        self.assertTrue(self.app.running)
        self.assertEqual(self.app.modal.text, "0")


class TestDigitsInsideOtherDialogs(DigitTestCase):
    def test_digits_do_nothing_in_the_viewer(self):
        self.cursor_to("메모.txt")
        self.app._on_key("3")
        viewer = self.app.modal

        self.app._on_key("8")  # 삭제로 새면 안 된다
        self.assertIs(self.app.modal, viewer)
        self.assertTrue((self.root / "메모.txt").exists())

    def test_digits_do_not_quit_from_the_help_screen(self):
        self.app._on_key("1")
        self.app._on_key("0")
        # MessageDialog 는 아무 키나 받으면 닫힌다. 종료로 이어지지 않아야 한다
        self.assertTrue(self.app.running)

    def test_digits_do_nothing_in_the_confirm_dialog(self):
        self.cursor_to("메모.txt")
        self.app._on_key("8")
        confirm = self.app.modal

        self.app._on_key("5")
        self.assertIs(self.app.modal, confirm)
        self.assertTrue((self.root / "메모.txt").exists())

    def test_digits_do_nothing_in_the_drive_dialog(self):
        fake = [
            {"path": "/", "label": "/", "kind": "루트"},
            {"path": "/Volumes/USB", "label": "USB", "kind": "볼륨"},
        ]
        with mock.patch.object(fsops, "list_drives", return_value=fake):
            self.app._on_key("\\")
            drive_dialog = self.app.modal
            self.app._on_key("2")
        self.assertIs(self.app.modal, drive_dialog)


class TestDigitsAreInputModeProof(DigitTestCase):
    def test_command_key_leaves_digits_untouched(self):
        for digit in "1234567890":
            self.assertEqual(term.command_key(digit), digit)

    def test_digits_work_the_same_regardless_of_hangul_mode(self):
        """IME 는 글자 키만 바꾼다. 숫자는 승인 창을 먹통으로 만든 문제가 없다."""
        self.app._on_key("4")
        self.assertIsInstance(self.app.modal, app_module.ShareDialog)


class TestHelpMentionsDigits(DigitTestCase):
    def test_help_lists_the_digit_shortcuts(self):
        """도움말이 길어 스크롤되므로 한 화면이 아니라 전체 내용을 본다."""
        self.app._on_key("1")
        full = "\n".join(app_module._HELP_LINES)
        self.assertIn("숫자", full)
        self.assertIn("1 . F1", full)
        self.assertIn("0 . F10", full)

    def test_help_can_scroll_to_the_digit_table(self):
        self.app._on_key("1")
        dialog = self.app.modal
        dialog.max_body = 16  # 24행 터미널 정도

        self.assertTrue(dialog.scrollable, "긴 도움말인데 스크롤이 안 된다")

        # 아래로 훑어가며 숫자 표가 어느 지점에서든 보이는지 확인한다
        seen = False
        for _ in range(len(app_module._HELP_LINES)):
            if "0 . F10" in "\n".join(dialog.lines(70)):
                seen = True
                break
            dialog.key(term.DOWN)
        self.assertTrue(seen, "스크롤해도 숫자 표에 닿지 못한다")

    def test_help_has_no_duplicate_entries(self):
        """넘치는 창에서 중복 줄은 실제 정보의 자리를 뺏는다."""
        keys = [l.split()[0] for l in app_module._HELP_LINES
                if l and not l.startswith(" ") and len(l.split()) > 1]
        duplicated = {k for k in keys if keys.count(k) > 1}
        self.assertEqual(duplicated, set(), "같은 키가 여러 번 설명되어 있습니다")


class _StubSession:
    peer_name = "stub-pc"
    peer_host = "127.0.0.1"

    def close(self, reason=None):
        pass


if __name__ == "__main__":
    unittest.main()
