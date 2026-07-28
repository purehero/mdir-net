# 공유 폴더 목록 — 저장·불러오기, 이름 정하기, 경로 해석, 관리 창 테스트

import json
import os
import tempfile
import unittest
from pathlib import Path

from mdirnet import fsops, shares as shares_mod
from mdirnet.ui import app as app_module
from mdirnet.ui import screen, term

from .helpers import make_shares


class ShareTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.config = self.base / "cfg" / "shares.json"

        self.downloads = self.base / "Downloads"
        self.project = self.base / "프로젝트"
        for d in (self.downloads, self.project):
            d.mkdir()

    def fresh(self, *paths):
        return make_shares(*paths, config_path=self.config)


class TestPathHelpers(unittest.TestCase):
    def test_join(self):
        self.assertEqual(shares_mod.join("a", "b"), "a/b")
        self.assertEqual(shares_mod.join(shares_mod.ROOT, "a"), "a")
        self.assertEqual(shares_mod.join(), shares_mod.ROOT)
        self.assertEqual(shares_mod.join("a", "b/c"), "a/b/c")

    def test_parent(self):
        self.assertEqual(shares_mod.parent("a/b/c"), "a/b")
        self.assertEqual(shares_mod.parent("a"), shares_mod.ROOT)
        self.assertEqual(shares_mod.parent(shares_mod.ROOT), shares_mod.ROOT)

    def test_display(self):
        self.assertEqual(shares_mod.display(shares_mod.ROOT), "/")
        self.assertEqual(shares_mod.display("a/b"), "/a/b")

    def test_normalize_resolves_dotdot(self):
        """`..` 을 버리지 말고 앞 조각을 지워야 한다. a/../b 는 b 다."""
        self.assertEqual(shares_mod.normalize("a/../b"), "b")
        self.assertEqual(shares_mod.normalize("a/b/../c"), "a/c")

    def test_normalize_never_goes_above_the_root(self):
        for text in ("..", "../..", "a/../..", "../../../etc"):
            got = shares_mod.normalize(text)
            self.assertNotIn("..", got, text)
            self.assertFalse(got.startswith("/"), text)

    def test_backslash_is_treated_as_a_separator(self):
        """Windows 상대가 역슬래시를 보내도 받아들인다."""
        self.assertEqual(shares_mod.normalize("a\\b"), "a/b")


class TestNaming(ShareTestCase):
    def test_name_comes_from_the_folder(self):
        shares = self.fresh()
        self.assertEqual(shares.add(self.downloads).name, "Downloads")
        self.assertEqual(shares.add(self.project).name, "프로젝트")

    def test_duplicate_names_get_a_suffix(self):
        other = self.base / "other" / "Downloads"
        other.mkdir(parents=True)

        shares = self.fresh()
        self.assertEqual(shares.add(self.downloads).name, "Downloads")
        self.assertEqual(shares.add(other).name, "Downloads (2)")

    def test_adding_the_same_folder_twice_is_a_no_op(self):
        shares = self.fresh()
        first = shares.add(self.downloads)
        second = shares.add(self.downloads)
        self.assertIs(first, second)
        self.assertEqual(len(shares), 1)

    def test_adding_a_file_is_refused(self):
        f = self.base / "notafolder.txt"
        f.write_bytes(b"x")
        with self.assertRaises(ValueError):
            self.fresh().add(f)

    def test_adding_a_missing_folder_is_refused(self):
        with self.assertRaises(ValueError):
            self.fresh().add(self.base / "없음")

    def test_remove(self):
        shares = self.fresh(self.downloads, self.project)
        self.assertTrue(shares.remove("Downloads"))
        self.assertEqual(shares.names, ["프로젝트"])
        self.assertFalse(shares.remove("Downloads"))


class TestPersistence(ShareTestCase):
    def test_save_then_load_round_trip(self):
        shares = self.fresh(self.downloads, self.project)
        shares.save()

        reloaded = shares_mod.ShareSet(config_path=self.config).load()
        self.assertEqual(reloaded.names, ["Downloads", "프로젝트"])
        self.assertEqual(
            [str(s.path) for s in reloaded], [str(self.downloads), str(self.project)]
        )

    def test_save_creates_the_directory(self):
        shares = self.fresh(self.downloads)
        self.assertFalse(self.config.parent.exists())
        shares.save()
        self.assertTrue(self.config.is_file())

    def test_saved_file_is_readable_json_with_hangul_intact(self):
        shares = self.fresh(self.project)
        shares.save()

        raw = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(raw[0]["name"], "프로젝트")
        self.assertIn("프로젝트", self.config.read_text(encoding="utf-8"))

    def test_missing_file_loads_as_empty(self):
        shares = shares_mod.ShareSet(config_path=self.base / "없는파일.json").load()
        self.assertEqual(len(shares), 0)

    def test_corrupt_file_loads_as_empty_instead_of_crashing(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text("{{{ 깨진 내용", encoding="utf-8")

        shares = shares_mod.ShareSet(config_path=self.config).load()
        self.assertEqual(len(shares), 0)

    def test_load_skips_bad_entries(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            json.dumps(
                [
                    {"name": "좋음", "path": str(self.downloads)},
                    {"name": "슬래시/포함", "path": str(self.project)},  # 이름에 / 는 금지
                    {"path": str(self.project)},  # 이름 없음
                    {"name": "경로없음"},
                    "문자열",
                    {"name": "좋음", "path": str(self.project)},  # 이름 중복
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        shares = shares_mod.ShareSet(config_path=self.config).load()
        self.assertEqual(shares.names, ["좋음"])

    def test_save_does_not_leave_a_temp_file(self):
        shares = self.fresh(self.downloads)
        shares.save()
        leftovers = [p.name for p in self.config.parent.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_load_keeps_a_share_whose_folder_disappeared(self):
        """외장 디스크를 뺐다고 목록에서 지워버리면 다시 꽂아도 복구되지 않는다."""
        gone = self.base / "usb"
        gone.mkdir()
        shares = self.fresh(gone)
        shares.save()
        gone.rmdir()

        reloaded = shares_mod.ShareSet(config_path=self.config).load()
        self.assertEqual(reloaded.names, ["usb"])
        self.assertFalse(next(iter(reloaded)).exists)


class TestResolution(ShareTestCase):
    def setUp(self):
        super().setUp()
        self.shares = self.fresh(self.downloads, self.project)
        (self.downloads / "sub").mkdir()
        (self.downloads / "sub" / "a.txt").write_bytes(b"x")

    def test_resolve_a_share_root(self):
        self.assertEqual(self.shares.resolve("Downloads"), self.downloads)

    def test_resolve_nested(self):
        self.assertEqual(
            self.shares.resolve("Downloads/sub/a.txt"), self.downloads / "sub" / "a.txt"
        )

    def test_resolve_picks_the_right_share(self):
        self.assertEqual(self.shares.resolve("프로젝트"), self.project)

    def test_unknown_share_is_denied(self):
        with self.assertRaises(fsops.PathDenied):
            self.shares.resolve("없는이름")

    def test_virtual_root_is_not_a_file(self):
        with self.assertRaises(fsops.PathDenied):
            self.shares.resolve(shares_mod.ROOT)

    def test_dotdot_cannot_escape_the_share(self):
        with self.assertRaises(fsops.PathDenied):
            self.shares.resolve("Downloads/../../etc/passwd")

    def test_symlink_out_of_the_share_is_denied(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(outside.rmdir)
        os.symlink(outside, self.downloads / "escape")

        with self.assertRaises(fsops.PathDenied):
            self.shares.resolve("Downloads/escape")

    def test_is_root(self):
        self.assertTrue(self.shares.is_root(shares_mod.ROOT))
        self.assertTrue(self.shares.is_root(""))
        self.assertTrue(self.shares.is_root("/"))
        self.assertFalse(self.shares.is_root("Downloads"))

    def test_share_of(self):
        self.assertEqual(self.shares.share_of("Downloads/sub").name, "Downloads")
        self.assertIsNone(self.shares.share_of(shares_mod.ROOT))

    def test_root_entries_are_directories_sorted_by_name(self):
        entries = self.shares.root_entries()
        self.assertEqual([e["name"] for e in entries], ["Downloads", "프로젝트"])
        self.assertTrue(all(e["is_dir"] for e in entries))

    def test_root_entries_hide_missing_folders(self):
        gone = self.base / "usb"
        gone.mkdir()
        self.shares.add(gone)
        gone.rmdir()

        self.assertEqual(
            [e["name"] for e in self.shares.root_entries()], ["Downloads", "프로젝트"]
        )


class TestShareDialog(ShareTestCase):
    def setUp(self):
        super().setUp()
        self.app = app_module.App(
            my_name="share-node",
            shares=self.fresh(self.downloads, self.project),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.refresh()

    def open_dialog(self):
        self.app._on_key("F4")
        self.assertIsInstance(self.app.modal, app_module.ShareDialog)
        return self.app.modal

    def test_lists_every_share_with_its_path(self):
        rendered = "\n".join(self.open_dialog().lines(70))
        self.assertIn("Downloads", rendered)
        self.assertIn("프로젝트", rendered)
        self.assertIn(str(self.downloads)[-20:], rendered)

    def test_explains_what_the_peer_sees(self):
        self.assertIn("최상위 폴더", "\n".join(self.open_dialog().lines(70)))

    def test_a_opens_the_folder_picker(self):
        self.open_dialog()
        self.app._on_key("a")
        self.assertIsInstance(self.app.modal, app_module.FolderDialog)

    def test_adding_through_the_picker_returns_to_the_manager(self):
        extra = self.base / "추가폴더"
        extra.mkdir()

        self.open_dialog()
        self.app._on_key("a")
        picker = self.app.modal
        picker.path = extra
        picker.key("CTRL-D")

        self.assertIn("추가폴더", self.app.shares.names)
        self.assertIsInstance(self.app.modal, app_module.ShareDialog)

    def test_d_asks_before_removing(self):
        dialog = self.open_dialog()
        dialog.cursor = 0
        self.app._on_key("d")

        self.assertIsInstance(self.app.modal, app_module.ConfirmDialog)
        rendered = "\n".join(self.app.modal.lines(60))
        self.assertIn("Downloads", rendered)
        self.assertIn("폴더와 파일은 그대로", rendered)
        self.assertEqual(len(self.app.shares), 2, "묻기만 하고 아직 지우면 안 된다")

    def test_confirming_removes_only_from_the_list(self):
        dialog = self.open_dialog()
        dialog.cursor = 0
        self.app._on_key("d")
        self.app._on_key("y")

        self.assertEqual(self.app.shares.names, ["프로젝트"])
        self.assertTrue(self.downloads.is_dir(), "실제 폴더는 남아야 한다")
        self.assertIsInstance(self.app.modal, app_module.ShareDialog)

    def test_declining_keeps_the_share(self):
        dialog = self.open_dialog()
        dialog.cursor = 0
        self.app._on_key("d")
        self.app._on_key("n")
        self.assertEqual(len(self.app.shares), 2)

    def test_missing_folder_is_marked(self):
        gone = self.base / "usb"
        gone.mkdir()
        self.app.shares.add(gone)
        gone.rmdir()

        self.assertIn("(없음)", "\n".join(self.open_dialog().lines(70)))

    def test_empty_list_says_so(self):
        self.app.shares.remove("Downloads")
        self.app.shares.remove("프로젝트")
        self.assertIn("공유하는 폴더가 없습니다", "\n".join(self.open_dialog().lines(70)))

    def test_cursor_clamps(self):
        dialog = self.open_dialog()
        for _ in range(20):
            dialog.key(term.DOWN)
        dialog.lines(70)
        self.assertEqual(dialog.cursor, len(self.app.shares) - 1)

    def test_esc_closes(self):
        self.open_dialog()
        self.app._on_key(term.ESC)
        self.assertIsNone(self.app.modal)

    def test_dialog_keeps_line_widths(self):
        self.open_dialog()
        for size in ((80, 24), (100, 30), (120, 40)):
            for line in self.app._compose(size):
                self.assertEqual(screen.width(line), size[0])

    def test_dialog_height_is_stable_whether_empty_or_full(self):
        heights = set()
        for count in (0, 1, 2):
            shares = self.fresh(*[self.downloads, self.project][:count])
            self.app.shares = shares
            heights.add(len(app_module.ShareDialog(self.app).lines(70)))
        self.assertEqual(len(heights), 1, heights)


class TestPersistOnChange(ShareTestCase):
    def make(self, persist):
        app = app_module.App(
            my_name="p",
            shares=self.fresh(),
            persist=persist,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(app.shutdown)
        return app

    def test_adding_saves_when_persisting(self):
        app = self.make(persist=True)
        app.add_share(self.downloads)
        self.assertTrue(self.config.is_file())
        self.assertIn("Downloads", self.config.read_text(encoding="utf-8"))

    def test_removing_saves_when_persisting(self):
        app = self.make(persist=True)
        app.add_share(self.downloads)
        app.add_share(self.project)
        app.remove_share("Downloads")

        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual([s["name"] for s in saved], ["프로젝트"])

    def test_nothing_is_written_when_not_persisting(self):
        """--share 로 준 폴더는 저장된 목록을 건드리지 않아야 한다."""
        app = self.make(persist=False)
        app.add_share(self.downloads)
        self.assertFalse(self.config.exists())

    def test_add_reports_a_bad_folder_instead_of_crashing(self):
        app = self.make(persist=False)
        self.assertIsNone(app.add_share(self.base / "없는폴더"))
        self.assertIsInstance(app.modal, app_module.MessageDialog)


if __name__ == "__main__":
    unittest.main()
