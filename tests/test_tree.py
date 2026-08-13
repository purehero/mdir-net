# 폴더 트리 (MCD) - 패널 자체가 트리가 된다
#
# 창을 띄우는 것이 아니다. MDir 도 NC 도 **패널을 바꿨다.** 그래서 확인할 것이
# 둘이다 - 트리 상태(펼침, 게으른 읽기)와, 그 상태에서 패널이 제대로 도는가.
#
# 이 기능의 값은 "빠르다" 는 것 하나다. 통째로 훑는 구현이었다면 홈 폴더에서
# 몇 초씩 화면이 멈춘다. 그래서 **읽은 폴더**를 직접 센다.

import tempfile
import unittest
from pathlib import Path

from mdirnet import fsops
from mdirnet.ui import app as app_module
from mdirnet.ui import screen, term

from .helpers import make_shares


class TreeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

        self.deep = self.root / "a" / "b" / "c"
        self.deep.mkdir(parents=True)
        for name in ("형제1", "형제2", "형제3"):
            (self.root / "a" / name).mkdir()
        (self.deep / "더깊이").mkdir()
        (self.deep / "메모.txt").write_bytes(b"x")

        self.app = app_module.App(
            my_name="tree-node",
            shares=make_shares(self.root),
            persist=False,
            tcp_port=0,
            udp_port=0,
            platform_name="testos",
            use_discovery=False,
        )
        self.addCleanup(self.app.shutdown)
        self.app.local.navigate(self.deep)
        self.app.focus = self.app.local

    @property
    def pane(self):
        return self.app.local

    def open_tree(self):
        self.app._on_key("4")
        return self.pane.tree

    def cursor_to(self, path):
        self.pane.cursor = self.pane.tree.index_of(path)

    def on_screen(self, path):
        return any(node == path for node, _ in self.pane.tree.rows)


class TestItIsThePaneNotAWindow(TreeTestCase):
    def test_pressing_4_changes_the_pane(self):
        self.open_tree()

        self.assertIsNotNone(self.pane.tree)
        self.assertIsNone(self.app.modal, "창을 띄웠다. 패널이 바뀌어야 한다")

    def test_pressing_4_again_goes_back(self):
        self.open_tree()
        self.app._on_key("4")

        self.assertIsNone(self.pane.tree)
        self.assertEqual(self.pane.path, self.deep, "되돌아왔는데 다른 폴더다")

    def test_the_rows_are_the_tree(self):
        self.open_tree()
        names = [r["name"] for r in self.pane.rows()]

        self.assertIn("a", names)
        self.assertIn("c", names)
        # 트리에는 `..` 이 없다. 뿌리가 이미 화면에 있다
        self.assertNotIn("..", names)

    def test_the_title_follows_the_cursor(self):
        self.open_tree()
        self.cursor_to(self.root / "a")

        self.assertEqual(self.pane.location, str(self.root / "a"))

    def test_it_draws_with_indent_and_a_marker(self):
        # 뿌리가 디스크 최상위라 화면에 다 담기지 않는다. 보려는 줄로 커서를
        # 옮겨 화면 안으로 끌어온 뒤에 본다
        self.open_tree()
        self.cursor_to(self.root / "a" / "형제1")
        text = "\n".join(self.app._compose((100, 30)))

        self.assertIn("- a", text, "펼친 것은 - 로 보여야 한다")
        self.assertIn("+ 형제1", text, "접힌 것은 + 로 보여야 한다")

    def test_depth_shows_as_indent(self):
        """깊이가 안 보이면 트리가 아니라 그냥 목록이다."""
        self.open_tree()
        self.cursor_to(self.deep)
        rows = {r["name"]: r["depth"] for r in self.pane.rows()}

        self.assertLess(rows["a"], rows["b"])
        self.assertLess(rows["b"], rows["c"])

    def test_the_other_pane_is_untouched(self):
        self.open_tree()

        self.assertIsNone(self.app.right.tree, "반대 패널까지 트리가 됐다")


class TestItOpensWhereYouAre(TreeTestCase):
    def test_the_cursor_starts_on_the_current_folder(self):
        self.open_tree()

        self.assertEqual(self.pane.tree.node_at(self.pane.cursor), self.deep)

    def test_only_the_ancestors_are_expanded(self):
        tree = self.open_tree()

        for parent in (self.root, self.root / "a", self.root / "a" / "b"):
            self.assertIn(parent, tree.expanded, "%s 가 안 펼쳐졌다" % parent)

        self.assertNotIn(self.deep, tree.expanded)
        self.assertNotIn(self.root / "a" / "형제1", tree.expanded)

    def test_it_starts_at_the_disk_root(self):
        tree = self.open_tree()

        self.assertTrue(fsops.is_filesystem_root(tree.root), tree.root)


class TestItReadsLazily(TreeTestCase):
    """여기가 이 기능의 핵심이다."""

    def test_opening_reads_only_the_ancestors(self):
        tree = self.open_tree()

        self.assertNotIn(self.root / "a" / "형제1", tree.children)
        self.assertNotIn(self.deep, tree.children)

    def test_expanding_reads_exactly_one_level(self):
        tree = self.open_tree()
        before = set(tree.children)

        self.cursor_to(self.root / "a" / "형제1")
        self.app._on_key(term.RIGHT)

        self.assertEqual(set(tree.children) - before, {self.root / "a" / "형제1"})

    def test_a_folder_is_read_once(self):
        tree = self.open_tree()
        target = self.root / "a" / "형제1"

        self.cursor_to(target)
        self.app._on_key(term.RIGHT)
        (target / "나중에 만든 것").mkdir()
        self.app._on_key(term.LEFT)
        self.app._on_key(term.RIGHT)

        self.assertNotIn(target / "나중에 만든 것", tree.children[target])


class TestMoving(TreeTestCase):
    def test_right_expands(self):
        self.open_tree()
        self.cursor_to(self.deep)
        self.app._on_key(term.RIGHT)

        self.assertTrue(self.on_screen(self.deep / "더깊이"))

    def test_only_folders_show_up(self):
        """트리는 폴더만 보여준다. 파일은 목록에서 볼 것이다."""
        self.open_tree()
        self.cursor_to(self.deep)
        self.app._on_key(term.RIGHT)

        self.assertFalse(self.on_screen(self.deep / "메모.txt"))

    def test_left_collapses_then_climbs(self):
        self.open_tree()
        self.cursor_to(self.deep)
        self.app._on_key(term.RIGHT)

        self.app._on_key(term.LEFT)
        self.assertFalse(self.on_screen(self.deep / "더깊이"))

        self.app._on_key(term.LEFT)
        self.assertEqual(self.pane.tree.node_at(self.pane.cursor), self.root / "a" / "b")

    def test_enter_opens_that_folder_and_leaves_the_tree(self):
        self.open_tree()
        self.cursor_to(self.root / "a" / "형제2")

        self.app._on_key(term.ENTER)

        self.assertIsNone(self.pane.tree, "트리에 머물면 파일을 다룰 수 없다")
        self.assertEqual(self.pane.path, self.root / "a" / "형제2")

    def test_the_cursor_stays_inside(self):
        self.open_tree()

        self.app._on_key(term.END_KEY)
        for _ in range(5):
            self.app._on_key(term.DOWN)
        self.assertEqual(self.pane.cursor, len(self.pane.rows()) - 1)

        self.app._on_key(term.HOME_KEY)
        for _ in range(5):
            self.app._on_key(term.UP)
        self.assertEqual(self.pane.cursor, 0)


class TestWhatIsRefused(TreeTestCase):
    def test_the_remote_pane_says_it_cannot(self):
        self.app.focus = self.app.remote
        self.app._on_key("4")

        self.assertIsNone(self.app.remote.tree)
        self.assertIn("상대 폴더는", self.app.status)

    def test_file_actions_are_refused_while_in_the_tree(self):
        """트리 줄은 파일이 아니다. 복사하려 들면 무엇을 복사할지 알 수 없다."""
        self.app.toggle_right_source()
        self.app.right.navigate(self.root)
        self.open_tree()

        self.app._start_copy()

        self.assertIn("트리 상태에서는", self.app.status)
        self.assertIsNone(self.app.modal)


class TestItSurvivesUnreadableFolders(TreeTestCase):
    def test_a_folder_it_cannot_read_keeps_the_tree_alive(self):
        blocked = self.root / "a" / "잠긴폴더"
        blocked.mkdir()
        blocked.chmod(0o000)
        self.addCleanup(blocked.chmod, 0o755)

        tree = self.open_tree()
        self.cursor_to(blocked)
        self.app._on_key(term.RIGHT)

        self.assertTrue(tree.rows)
        self.assertIn("읽을 수 없습니다", tree.message)
        self.assertEqual(tree.children[blocked], [])


class TestDrawing(TreeTestCase):
    def test_every_line_is_exactly_terminal_width(self):
        """트리 줄이 한 칸이라도 넘치면 줄바꿈이 생겨 화면 전체가 밀린다."""
        self.open_tree()
        for size in ((80, 24), (100, 30), (40, 12), (200, 60)):
            for index, line in enumerate(self.app._compose(size)):
                self.assertEqual(
                    screen.width(line), size[0], "size=%s line %d: %r" % (size, index, line)
                )

    def test_a_deep_tree_does_not_overflow(self):
        """들여쓰기가 폭을 넘어서면 줄이 길어진다. 깊은 곳에서 확인한다."""
        deeper = self.root
        for step in range(20):
            deeper = deeper / ("깊이%02d" % step)
        deeper.mkdir(parents=True)
        self.app.local.navigate(deeper)
        self.open_tree()

        for index, line in enumerate(self.app._compose((60, 24))):
            self.assertEqual(screen.width(line), 60, "line %d: %r" % (index, line))


if __name__ == "__main__":
    unittest.main()
