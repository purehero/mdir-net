# 폴더 트리 (MCD) - 펼칠 때 읽는가, 그리고 못 읽는 폴더에서 죽지 않는가
#
# 이 다이얼로그의 값은 "빠르다" 는 것 하나다. 통째로 훑는 구현이었다면 홈 폴더에서
# 몇 초, 넓은 디스크에서 몇 분씩 화면이 멈춘다. 그래서 **읽은 폴더 수**를 직접 센다.

import tempfile
import unittest
from pathlib import Path

from mdirnet import fsops
from mdirnet.ui import app as app_module
from mdirnet.ui import term


class TreeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

        # 깊이 3, 가지마다 형제를 여럿 둔다
        self.deep = self.root / "a" / "b" / "c"
        self.deep.mkdir(parents=True)
        for name in ("형제1", "형제2", "형제3"):
            (self.root / "a" / name).mkdir()
        for name in ("깊은형제1", "깊은형제2"):
            (self.root / "a" / "b" / name).mkdir()
        (self.deep / "더깊이").mkdir()

    def make(self, start=None):
        self.chosen = []
        self.cancelled = []
        dialog = app_module.TreeDialog(
            start or self.deep,
            on_choose=self.chosen.append,
            on_cancel=lambda: self.cancelled.append(True),
        )
        dialog.max_body = 24
        return dialog

    @staticmethod
    def on_screen(dialog, path):
        """트리 줄 목록에 그 경로가 있는가. 깊이는 디스크 뿌리에 따라 달라진다."""
        return any(node == path for node, _ in dialog.rows)

    def shown(self, dialog):
        """화면에 나온 이름들 (장식 제거)."""
        out = []
        for line in dialog.lines(60):
            plain = term.strip(line) if hasattr(term, "strip") else line
            out.append(plain)
        return out


class TestItOpensWhereYouAre(TreeTestCase):
    def test_the_cursor_starts_on_the_current_folder(self):
        dialog = self.make()
        self.assertEqual(dialog.current, self.deep)

    def test_only_the_ancestors_are_expanded(self):
        dialog = self.make()

        for parent in (self.root, self.root / "a", self.root / "a" / "b"):
            self.assertIn(parent, dialog.expanded, "%s 가 안 펼쳐졌다" % parent)

        # 지금 폴더 자신과 형제들은 접혀 있어야 한다
        self.assertNotIn(self.deep, dialog.expanded)
        self.assertNotIn(self.root / "a" / "형제1", dialog.expanded)

    def test_it_starts_at_the_disk_root(self):
        dialog = self.make()
        self.assertTrue(fsops.is_filesystem_root(dialog.root), dialog.root)


class TestItReadsLazily(TreeTestCase):
    """여기가 이 기능의 핵심이다."""

    def test_opening_reads_only_the_ancestors(self):
        dialog = self.make()

        # 조상 사슬만 읽혔다. 형제들 안쪽은 손대지 않았다
        self.assertNotIn(self.root / "a" / "형제1", dialog.children)
        self.assertNotIn(self.deep, dialog.children)

    def test_expanding_reads_exactly_one_level(self):
        dialog = self.make()
        before = set(dialog.children)

        dialog.cursor = dialog._index_of(self.root / "a" / "형제1")
        dialog.key(term.RIGHT)

        added = set(dialog.children) - before
        self.assertEqual(added, {self.root / "a" / "형제1"})

    def test_a_folder_is_read_once(self):
        dialog = self.make()
        target = self.root / "a" / "형제1"

        dialog.cursor = dialog._index_of(target)
        dialog.key(term.RIGHT)
        dialog.key(term.LEFT)   # 접고
        dialog.key(term.RIGHT)  # 다시 펼친다

        # 두 번째 펼침은 기억한 것을 쓴다. 새 폴더를 만들어 확인한다
        (target / "나중에 만든 것").mkdir()
        dialog.key(term.LEFT)
        dialog.key(term.RIGHT)
        self.assertNotIn(target / "나중에 만든 것", dialog.children[target])


class TestMoving(TreeTestCase):
    def test_expand_shows_the_children(self):
        dialog = self.make()
        dialog.cursor = dialog._index_of(self.deep)
        dialog.key(term.RIGHT)

        self.assertIn(self.deep, dialog.expanded)
        self.assertTrue(self.on_screen(dialog, self.deep / "더깊이"))

    def test_collapse_hides_them_again(self):
        dialog = self.make()
        dialog.cursor = dialog._index_of(self.deep)
        dialog.key(term.RIGHT)
        dialog.key(term.LEFT)

        self.assertNotIn(self.deep, dialog.expanded)
        self.assertFalse(self.on_screen(dialog, self.deep / "더깊이"))

    def test_collapsing_a_collapsed_folder_goes_to_the_parent(self):
        """깊이 들어갔을 때 되돌아 나오는 길이다."""
        dialog = self.make()
        self.assertEqual(dialog.current, self.deep)

        dialog.key(term.LEFT)

        self.assertEqual(dialog.current, self.root / "a" / "b")

    def test_enter_chooses_and_closes(self):
        dialog = self.make()
        closed = dialog.key(term.ENTER)

        self.assertTrue(closed)
        self.assertEqual(self.chosen, [self.deep])

    def test_esc_cancels(self):
        dialog = self.make()
        closed = dialog.key(term.ESC)

        self.assertTrue(closed)
        self.assertEqual(self.chosen, [])
        self.assertEqual(self.cancelled, [True])

    def test_the_cursor_stays_inside_the_list(self):
        """끝에서 더 눌러도 넘어가지 않는다. 조상 중에 항목이 수만 개인 폴더가
        있을 수 있어서(임시 폴더가 그렇다) 몇 번 눌러 끝에 닿을지는 알 수 없다."""
        dialog = self.make()

        dialog.key(term.END_KEY)
        for _ in range(5):
            dialog.key(term.DOWN)
        self.assertEqual(dialog.cursor, len(dialog.rows) - 1)

        dialog.key(term.HOME_KEY)
        for _ in range(5):
            dialog.key(term.UP)
        self.assertEqual(dialog.cursor, 0)


class TestItSurvivesUnreadableFolders(TreeTestCase):
    def test_a_folder_it_cannot_read_shows_a_message_and_keeps_going(self):
        blocked = self.root / "a" / "잠긴폴더"
        blocked.mkdir()
        blocked.chmod(0o000)
        self.addCleanup(blocked.chmod, 0o755)

        dialog = self.make()
        dialog.cursor = dialog._index_of(blocked)
        dialog.key(term.RIGHT)

        # 트리는 살아 있고, 이유는 화면에 남는다
        self.assertTrue(dialog.rows)
        self.assertIn("읽을 수 없습니다", dialog.message)
        self.assertEqual(dialog.children[blocked], [])


class TestLowScreens(TreeTestCase):
    def test_the_hint_line_survives_a_short_screen(self):
        """폰의 터미널처럼 낮은 화면에서 안내줄이 사라지면 나가는 길을 알 수 없다."""
        for height in range(6, 26):
            dialog = self.make()
            dialog.max_body = height
            lines = dialog.lines(50)

            self.assertLessEqual(len(lines), height, "%d줄에서 넘쳤다" % height)
            self.assertIn("Enter 이 폴더로", lines[-1], "%d줄에서 안내줄이 사라졌다" % height)


if __name__ == "__main__":
    unittest.main()
