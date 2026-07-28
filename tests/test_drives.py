# 드라이브/볼륨 선택 기능 테스트
#
# Windows 실기에서 돌릴 수 없으므로, 드라이브 열거는 ctypes 호출을 가짜로 바꿔
# 로직만 검증한다. POSIX 볼륨 열거는 이 기계에서 그대로 확인한다.

import os
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

from mdirnet import fsops
from mdirnet.ui import app as app_module
from mdirnet.ui import screen, term

from .helpers import make_shares, only_name, rpath




class TestIsFilesystemRoot(unittest.TestCase):
    def test_posix(self):
        self.assertTrue(fsops.is_filesystem_root("/"))
        self.assertFalse(fsops.is_filesystem_root("/Users"))

    def test_windows_drive_root_is_its_own_parent(self):
        """이것이 드라이브 선택이 필요한 근본 이유다. C:\\ 위로는 못 올라간다."""
        drive = PureWindowsPath("C:\\")
        self.assertEqual(drive, drive.parent)
        self.assertNotEqual(PureWindowsPath(r"C:\Users"), PureWindowsPath(r"C:\Users").parent)


class TestWindowsDrives(unittest.TestCase):
    """비트마스크 해석과 종류 표시를 가짜 ctypes 로 검증한다."""

    def fake(self, bits, kinds=None):
        kinds = kinds or {}
        return (
            mock.patch.object(fsops, "_windows_drive_bits", return_value=bits),
            mock.patch.object(fsops, "_windows_drive_kind", side_effect=lambda r: kinds.get(r, "")),
        )

    def test_bitmask_selects_the_right_letters(self):
        # A(0), C(2), D(3) 비트만 켠다
        bits = (1 << 0) | (1 << 2) | (1 << 3)
        with self.fake(bits)[0], self.fake(bits)[1]:
            drives = fsops._windows_drives()
        self.assertEqual([d["label"] for d in drives], ["A:", "C:", "D:"])
        self.assertEqual([d["path"] for d in drives], ["A:\\", "C:\\", "D:\\"])

    def test_kind_is_reported(self):
        bits = 1 << 2
        kinds = {"C:\\": "고정"}
        with self.fake(bits, kinds)[0], self.fake(bits, kinds)[1]:
            drives = fsops._windows_drives()
        self.assertEqual(drives[0]["kind"], "고정")

    def test_no_drives_returns_empty(self):
        with self.fake(0)[0], self.fake(0)[1]:
            self.assertEqual(fsops._windows_drives(), [])

    def test_falls_back_to_probing_when_bitmask_unavailable(self):
        """GetLogicalDrives 를 못 부르면 존재 확인으로 물러난다."""
        with mock.patch.object(fsops, "_windows_drive_bits", return_value=None), mock.patch.object(
            fsops, "_windows_drive_kind", return_value=""
        ), mock.patch.object(fsops, "Path") as fake_path:
            fake_path.side_effect = lambda p: mock.Mock(exists=lambda: p in ("C:\\", "E:\\"))
            drives = fsops._windows_drives()
        self.assertEqual([d["label"] for d in drives], ["C:", "E:"])

    def test_all_26_letters_are_considered(self):
        with self.fake((1 << 26) - 1)[0], self.fake((1 << 26) - 1)[1]:
            drives = fsops._windows_drives()
        self.assertEqual(len(drives), 26)
        self.assertEqual(drives[0]["label"], "A:")
        self.assertEqual(drives[-1]["label"], "Z:")

    def test_list_drives_dispatches_to_windows_on_nt(self):
        with mock.patch.object(fsops.os, "name", "nt"), mock.patch.object(
            fsops, "_windows_drives", return_value=[{"path": "C:\\", "label": "C:", "kind": ""}]
        ) as win:
            result = fsops.list_drives()
        win.assert_called_once()
        self.assertEqual(result[0]["label"], "C:")


class TestPosixVolumes(unittest.TestCase):
    def test_root_is_always_first(self):
        drives = fsops._posix_volumes()
        self.assertEqual(drives[0]["path"], "/")
        self.assertEqual(drives[0]["kind"], "루트")

    def test_no_duplicate_paths(self):
        paths = [d["path"] for d in fsops._posix_volumes()]
        self.assertEqual(len(paths), len(set(paths)))

    def test_mounted_volumes_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "USB").mkdir()
            (base / "백업디스크").mkdir()
            (base / "파일.txt").write_bytes(b"x")  # 파일은 볼륨이 아니다

            with mock.patch.object(fsops, "_POSIX_VOLUME_DIRS", (str(base),)):
                drives = fsops._posix_volumes()

        labels = [d["label"] for d in drives]
        self.assertIn("USB", labels)
        self.assertIn("백업디스크", labels)
        self.assertNotIn("파일.txt", labels)

    def test_unreadable_volume_dir_is_skipped(self):
        with mock.patch.object(fsops, "_POSIX_VOLUME_DIRS", ("/nonexistent-xyz",)):
            drives = fsops._posix_volumes()
        self.assertEqual([d["path"] for d in drives], ["/"])

    def test_list_drives_uses_posix_here(self):
        self.assertEqual(fsops.list_drives()[0]["path"], "/")


class TestDriveDialog(unittest.TestCase):
    DRIVES = [
        {"path": "C:\\", "label": "C:", "kind": "고정"},
        {"path": "D:\\", "label": "D:", "kind": "이동식"},
        {"path": "Z:\\", "label": "Z:", "kind": "네트워크"},
    ]

    def make(self, drives=None):
        chosen = []
        return chosen, app_module.DriveDialog(
            self.DRIVES if drives is None else drives, chosen.append
        )

    def test_lists_every_drive_with_kind(self):
        _, dialog = self.make()
        rendered = "\n".join(dialog.lines(60))
        for label in ("C:", "D:", "Z:"):
            self.assertIn(label, rendered)
        self.assertIn("이동식", rendered)
        self.assertIn("네트워크", rendered)

    def test_enter_chooses_the_highlighted_drive(self):
        chosen, dialog = self.make()
        dialog.key(term.DOWN)
        self.assertTrue(dialog.key(term.ENTER))
        self.assertEqual(chosen, ["D:\\"])

    def test_cursor_clamps(self):
        _, dialog = self.make()
        for _ in range(20):
            dialog.key(term.DOWN)
        self.assertEqual(dialog.cursor, len(self.DRIVES) - 1)
        for _ in range(20):
            dialog.key(term.UP)
        self.assertEqual(dialog.cursor, 0)

    def test_esc_cancels_without_choosing(self):
        chosen, dialog = self.make()
        self.assertTrue(dialog.key(term.ESC))
        self.assertEqual(chosen, [])

    def test_empty_list_says_so_and_enter_does_nothing(self):
        chosen, dialog = self.make(drives=[])
        self.assertIn("없습니다", "\n".join(dialog.lines(60)))
        self.assertFalse(dialog.key(term.ENTER))
        self.assertEqual(chosen, [])

    def test_highlight_moves_with_the_cursor(self):
        _, dialog = self.make()
        first = dialog.lines(60)
        self.assertIn(term.REVERSE, first[0])
        dialog.key(term.DOWN)
        second = dialog.lines(60)
        self.assertNotIn(term.REVERSE, second[0])
        self.assertIn(term.REVERSE, second[1])


class DriveAppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "폴더").mkdir()

        self.app = app_module.App(
            my_name="drive-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.refresh()


class TestDriveKeyInApp(DriveAppTestCase):
    FAKE = [
        {"path": "/", "label": "/", "kind": "루트"},
        {"path": "/Volumes/USB", "label": "USB", "kind": "볼륨"},
    ]

    def test_backslash_opens_the_drive_dialog(self):
        with mock.patch.object(fsops, "list_drives", return_value=self.FAKE):
            self.app._on_key("\\")
        self.assertIsInstance(self.app.modal, app_module.DriveDialog)

    def test_choosing_moves_the_local_pane(self):
        with mock.patch.object(fsops, "list_drives", return_value=self.FAKE):
            self.app._on_key("\\")
            self.app._on_key(term.ENTER)  # '/' 선택

        self.assertIsNone(self.app.modal)
        self.assertEqual(self.app.local.path, Path("/"))

    def test_single_drive_reports_nothing_to_switch_to(self):
        with mock.patch.object(fsops, "list_drives", return_value=self.FAKE[:1]):
            self.app._on_key("\\")
        self.assertIsNone(self.app.modal)
        self.assertIn("옮겨갈 드라이브가 없습니다", self.app.status)

    def test_remote_pane_is_refused_with_a_reason(self):
        """상대는 공유 루트에 갇혀 있어 드라이브를 바꿔도 곧바로 거부된다."""
        self.app.session = _StubSession()
        self.app.focus = self.app.remote

        with mock.patch.object(fsops, "list_drives", return_value=self.FAKE):
            self.app._on_key("\\")

        self.assertIsNone(self.app.modal)
        self.assertIn("공유 폴더 안으로 제한", self.app.status)

    def test_overlay_keeps_line_widths(self):
        with mock.patch.object(fsops, "list_drives", return_value=self.FAKE):
            self.app._on_key("\\")
        for size in ((80, 24), (100, 30), (40, 12)):
            for line in self.app._compose(size):
                self.assertEqual(screen.width(line), size[0])

    def test_pane_footer_hints_at_the_drive_key_when_at_root(self):
        self.app.local.navigate(Path("/"))
        body = "\n".join(self.app._compose((100, 30)))
        self.assertIn("드라이브 선택", body)

    def test_pane_footer_has_no_hint_when_not_at_root(self):
        summary = self.app._pane_summary(self.app.local)
        self.assertNotIn("드라이브", summary)


class TestFolderDialogDriveRoute(DriveAppTestCase):
    """공유 폴더 선택상자에서도 다른 드라이브로 갈 수 있어야 한다.

    이게 없으면 Windows 사용자는 D: 의 폴더를 공유할 방법이 없다.
    """

    def test_drive_row_appears_at_a_filesystem_root(self):
        opened = []
        dialog = app_module.FolderDialog(
            "/", on_choose=lambda p: None, on_cancel=lambda: None, on_drives=lambda: opened.append(1)
        )
        self.assertEqual(dialog.rows()[0], app_module.FolderDialog.DRIVE_ROW)

    def test_dotdot_appears_when_not_at_a_root(self):
        dialog = app_module.FolderDialog(
            self.root, on_choose=lambda p: None, on_cancel=lambda: None, on_drives=lambda: None
        )
        self.assertEqual(dialog.rows()[0], "..")

    def test_no_drive_row_when_no_handler_is_wired(self):
        dialog = app_module.FolderDialog("/", on_choose=lambda p: None, on_cancel=lambda: None)
        self.assertNotIn(app_module.FolderDialog.DRIVE_ROW, dialog.rows())

    def test_enter_on_the_drive_row_opens_the_drive_list(self):
        opened = []
        dialog = app_module.FolderDialog(
            "/", on_choose=lambda p: None, on_cancel=lambda: None, on_drives=lambda: opened.append(1)
        )
        dialog.cursor = 0
        dialog.key(term.ENTER)
        self.assertEqual(opened, [1])

    def test_backslash_also_opens_the_drive_list(self):
        opened = []
        dialog = app_module.FolderDialog(
            self.root, on_choose=lambda p: None, on_cancel=lambda: None, on_drives=lambda: opened.append(1)
        )
        self.assertFalse(dialog.key("\\"))
        self.assertEqual(opened, [1])

    def test_footer_mentions_the_drive_key(self):
        dialog = app_module.FolderDialog(
            self.root, on_choose=lambda p: None, on_cancel=lambda: None, on_drives=lambda: None
        )
        self.assertIn("드라이브", "\n".join(dialog.lines(60)))

    def test_share_picker_can_reach_a_drive_and_come_back(self):
        """F4 > a(추가) > \\ > 드라이브 고르기 > 그 루트에서 선택상자가 열린다."""
        fake = [{"path": "/", "label": "/", "kind": "루트"}]
        with mock.patch.object(fsops, "list_drives", return_value=fake):
            self.app._on_key("F4")
            self.assertIsInstance(self.app.modal, app_module.ShareDialog)

            self.app._on_key("a")  # 추가 → 폴더 선택상자
            self.assertIsInstance(self.app.modal, app_module.FolderDialog)

            self.app._on_key("\\")
            self.assertIsInstance(self.app.modal, app_module.DriveDialog)

            self.app._on_key(term.ENTER)

        self.assertIsInstance(self.app.modal, app_module.FolderDialog)
        self.assertEqual(self.app.modal.path, Path("/"))

    def test_choosing_a_folder_after_a_drive_hop_adds_a_share(self):
        fake = [{"path": str(self.root), "label": "T:", "kind": "고정"}]
        with mock.patch.object(fsops, "list_drives", return_value=fake):
            self.app._on_key("F4")
            self.app._on_key("a")
            self.app._on_key("\\")
            self.app._on_key(term.ENTER)  # self.root 로 이동

        picker = self.app.modal
        picker.cursor = picker.rows().index("폴더")
        picker.key(term.ENTER)
        picker.key("CTRL-D")

        self.assertIn("폴더", self.app.shares.names)
        # Server 가 같은 목록 객체를 보므로 새 세션에 곧바로 반영된다
        self.assertIs(self.app.server.shares, self.app.shares)


class _StubSession:
    peer_name = "stub-pc"
    peer_host = "127.0.0.1"

    def close(self, reason=None):
        pass


if __name__ == "__main__":
    unittest.main()
