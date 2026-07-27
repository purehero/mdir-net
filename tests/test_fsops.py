# 디렉터리 조회, 재귀 walk, 경로 탈출 차단을 확인하는 테스트

import os
import tempfile
import unittest
from pathlib import Path

from mdirnet import fsops


class FsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def touch(self, rel, data=b""):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class TestListDir(FsTestCase):
    def test_directories_come_first_then_names(self):
        self.touch("b.txt")
        self.touch("A.txt")
        (self.root / "zdir").mkdir()
        (self.root / "Adir").mkdir()

        names = [e["name"] for e in fsops.list_dir(self.root)]
        self.assertEqual(names, ["Adir", "zdir", "A.txt", "b.txt"])

    def test_reports_size_and_dir_flag(self):
        self.touch("data.bin", b"x" * 1234)
        (self.root / "sub").mkdir()

        by_name = {e["name"]: e for e in fsops.list_dir(self.root)}
        self.assertEqual(by_name["data.bin"]["size"], 1234)
        self.assertFalse(by_name["data.bin"]["is_dir"])
        self.assertTrue(by_name["sub"]["is_dir"])
        self.assertEqual(by_name["sub"]["size"], 0)

    def test_broken_symlink_does_not_break_listing(self):
        self.touch("real.txt", b"hi")
        os.symlink(self.root / "nope", self.root / "dangling")

        names = [e["name"] for e in fsops.list_dir(self.root)]
        self.assertIn("dangling", names)
        self.assertIn("real.txt", names)


class TestResolveWithin(FsTestCase):
    def test_allows_root_itself(self):
        self.assertEqual(fsops.resolve_within(self.root, self.root), self.root)

    def test_allows_nested_path(self):
        target = self.touch("a/b/c.txt")
        self.assertEqual(fsops.resolve_within(self.root, target), target)

    def test_relative_path_is_joined_to_root(self):
        self.touch("a/b.txt")
        self.assertEqual(fsops.resolve_within(self.root, "a/b.txt"), self.root / "a" / "b.txt")

    def test_allows_nonexistent_path_for_incoming_files(self):
        """수신 대상 파일은 아직 없다. 그래도 검사는 통과해야 한다."""
        expected = self.root / "new" / "incoming.bin"
        self.assertEqual(fsops.resolve_within(self.root, expected), expected)

    def test_blocks_dotdot_escape(self):
        with self.assertRaises(fsops.PathDenied):
            fsops.resolve_within(self.root, self.root / ".." / ".." / "etc" / "passwd")

    def test_blocks_absolute_outside_path(self):
        with self.assertRaises(fsops.PathDenied):
            fsops.resolve_within(self.root, "/etc/passwd")

    def test_blocks_symlink_pointing_outside(self):
        """심링크를 따라간 결과가 루트 밖이면 거부해야 한다."""
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: outside.rmdir())
        os.symlink(outside, self.root / "escape")

        with self.assertRaises(fsops.PathDenied):
            fsops.resolve_within(self.root, self.root / "escape")

    def test_blocks_sibling_with_shared_prefix(self):
        """/root 와 /rootlet 처럼 앞부분이 같은 형제 디렉터리를 통과시키면 안 된다."""
        sibling = self.root.parent / (self.root.name + "let")
        sibling.mkdir()
        self.addCleanup(sibling.rmdir)

        with self.assertRaises(fsops.PathDenied):
            fsops.resolve_within(self.root, sibling)


class TestWalkTree(FsTestCase):
    def test_collects_files_with_relative_posix_paths(self):
        self.touch("top.txt", b"1")
        self.touch("sub/mid.txt", b"22")
        self.touch("sub/deep/leaf.bin", b"333")

        files = fsops.walk_tree(self.root)
        self.assertEqual(
            {f["path"]: f["size"] for f in files},
            {"top.txt": 1, "sub/mid.txt": 2, "sub/deep/leaf.bin": 3},
        )

    def test_skips_symlinked_files(self):
        self.touch("real.txt", b"abc")
        os.symlink(self.root / "real.txt", self.root / "link.txt")

        paths = [f["path"] for f in fsops.walk_tree(self.root)]
        self.assertEqual(paths, ["real.txt"])

    def test_empty_directory_yields_nothing(self):
        (self.root / "empty").mkdir()
        self.assertEqual(fsops.walk_tree(self.root), [])


class TestHumanSize(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(fsops.human_size(0), "0 B")
        self.assertEqual(fsops.human_size(999), "999 B")
        self.assertEqual(fsops.human_size(1024), "1.0 KB")
        self.assertEqual(fsops.human_size(1536), "1.5 KB")
        self.assertEqual(fsops.human_size(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(fsops.human_size(3 * 1024 ** 3), "3.0 GB")

    def test_absurdly_large_still_returns_a_string(self):
        self.assertTrue(fsops.human_size(10 ** 20).endswith("TB"))


if __name__ == "__main__":
    unittest.main()
