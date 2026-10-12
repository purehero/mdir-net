# 압축 파일 읽기 - 목록, 보기, 꺼내기
#
# 위험한 곳은 꺼내기 한 군데다. 압축 안의 이름은 **만든 쪽이 정하는 값**이라
# `../../etc/passwd` 같은 것이 들어 있을 수 있다(zip slip). 그 검사를 제일 먼저 본다.

import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from mdirnet import archive


class ArchiveTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def make_zip(self, name="묶음.zip", names=None):
        path = self.root / name
        names = names or {
            "읽어보세요.txt": b"top level",
            "안쪽/메모.txt": b"inside",
            "안쪽/더안쪽/깊은것.bin": b"x" * 100,
        }
        with zipfile.ZipFile(path, "w") as zf:
            for inner, data in names.items():
                zf.writestr(inner, data)
        return path

    def make_tar(self, name="묶음.tar.gz"):
        path = self.root / name
        stage = self.root / "stage"
        (stage / "안쪽").mkdir(parents=True)
        (stage / "위.txt").write_bytes(b"top")
        (stage / "안쪽" / "속.txt").write_bytes(b"deep")
        with tarfile.open(path, "w:gz") as tf:
            tf.add(stage / "위.txt", arcname="위.txt")
            tf.add(stage / "안쪽", arcname="안쪽")
        return path


class TestZipSlip(ArchiveTestCase):
    """여기가 이 모듈의 핵심이다."""

    def test_a_name_that_climbs_out_is_refused(self):
        for evil in ("../밖.txt", "../../더밖.txt", "안쪽/../../밖.txt"):
            with self.subTest(evil):
                with self.assertRaises(ValueError):
                    archive.safe_join(self.root / "여기", evil)

    def test_an_absolute_name_lands_inside_not_at_the_root(self):
        """`/etc/passwd` 가 진짜 /etc 를 가리키면 안 된다."""
        landed = archive.safe_join(self.root / "여기", "/etc/passwd")

        self.assertEqual(landed, (self.root / "여기" / "etc" / "passwd").resolve())

    def test_a_windows_style_name_is_handled_too(self):
        landed = archive.safe_join(self.root / "여기", "안쪽\\메모.txt")

        self.assertEqual(landed, (self.root / "여기" / "안쪽" / "메모.txt").resolve())

    def test_a_normal_name_passes(self):
        landed = archive.safe_join(self.root / "여기", "안쪽/메모.txt")

        self.assertEqual(landed, (self.root / "여기" / "안쪽" / "메모.txt").resolve())

    def test_an_escaping_entry_is_hidden_from_the_listing(self):
        """목록에 보이면 패널의 `..` 과 섞여 헷갈리고, 고를 수도 있게 된다."""
        path = self.make_zip(names={"../../탈출.txt": b"out", "정상.txt": b"ok"})

        self.assertEqual({e["name"] for e in archive.list_dir(path)}, {"정상.txt"})
        self.assertEqual(archive.walk(path), [("정상.txt", 2)])

    def test_but_how_many_were_hidden_is_reported(self):
        """조용히 지우면 사용자는 압축이 온전한 줄 안다."""
        path = self.make_zip(names={"../a.txt": b"1", "../../b.txt": b"2", "ok.txt": b"3"})

        self.assertEqual(archive.escaping_count(path), 2)

    def test_a_clean_archive_reports_nothing_hidden(self):
        self.assertEqual(archive.escaping_count(self.make_zip()), 0)

    def test_the_root_itself_is_allowed(self):
        self.assertEqual(
            archive.safe_join(self.root / "여기", ""), (self.root / "여기").resolve()
        )


class TestListing(ArchiveTestCase):
    def test_the_top_level_shows_folders_and_files(self):
        path = self.make_zip()
        rows = {e["name"]: e for e in archive.list_dir(path)}

        self.assertEqual(set(rows), {"읽어보세요.txt", "안쪽"})
        self.assertTrue(rows["안쪽"]["is_dir"])
        self.assertFalse(rows["읽어보세요.txt"]["is_dir"])

    def test_a_folder_that_is_not_an_entry_still_shows(self):
        """압축에는 폴더 항목이 없을 수 있다. 이름을 조각내어 만들어야 한다."""
        path = self.make_zip(names={"없는폴더/속.txt": b"x"})
        rows = {e["name"]: e for e in archive.list_dir(path)}

        self.assertEqual(set(rows), {"없는폴더"})
        self.assertTrue(rows["없는폴더"]["is_dir"])

    def test_going_deeper_shows_only_that_level(self):
        path = self.make_zip()
        rows = {e["name"]: e for e in archive.list_dir(path, "안쪽")}

        self.assertEqual(set(rows), {"메모.txt", "더안쪽"})

        deeper = {e["name"] for e in archive.list_dir(path, "안쪽/더안쪽")}
        self.assertEqual(deeper, {"깊은것.bin"})

    def test_sizes_come_through(self):
        path = self.make_zip()
        rows = {e["name"]: e for e in archive.list_dir(path, "안쪽/더안쪽")}

        self.assertEqual(rows["깊은것.bin"]["size"], 100)

    def test_tar_gz_works_the_same(self):
        path = self.make_tar()
        rows = {e["name"]: e for e in archive.list_dir(path)}

        self.assertEqual(set(rows), {"위.txt", "안쪽"})
        self.assertTrue(rows["안쪽"]["is_dir"])
        self.assertEqual({e["name"] for e in archive.list_dir(path, "안쪽")}, {"속.txt"})

    def test_a_broken_archive_raises_instead_of_crashing(self):
        broken = self.root / "깨진.zip"
        broken.write_bytes("이건 압축이 아니다".encode("utf-8"))

        with self.assertRaises(archive.ArchiveError):
            archive.list_dir(broken)


class TestReadingAndExtracting(ArchiveTestCase):
    def test_read_head_returns_the_front(self):
        path = self.make_zip()
        size, head = archive.read_head(path, "안쪽/더안쪽/깊은것.bin", 10)

        self.assertEqual(size, 100)
        self.assertEqual(head, b"x" * 10)

    def test_extract_writes_the_file(self):
        path = self.make_zip()
        dest = self.root / "꺼낸곳" / "메모.txt"

        archive.extract(path, "안쪽/메모.txt", dest)

        self.assertEqual(dest.read_bytes(), b"inside")

    def test_a_missing_name_raises(self):
        path = self.make_zip()

        with self.assertRaises(archive.ArchiveError):
            archive.extract(path, "없는것.txt", self.root / "x")

    def test_walk_flattens_only_files(self):
        path = self.make_zip()
        found = dict(archive.walk(path, "안쪽"))

        self.assertEqual(
            set(found), {"안쪽/메모.txt", "안쪽/더안쪽/깊은것.bin"}
        )
        self.assertEqual(found["안쪽/더안쪽/깊은것.bin"], 100)


class TestWhatCountsAsAnArchive(ArchiveTestCase):
    def test_known_suffixes(self):
        for name in ("a.zip", "a.tar", "a.tar.gz", "a.tgz", "a.TAR.GZ", "a.Zip"):
            self.assertTrue(archive.looks_like_archive(name), name)

    def test_others_are_not(self):
        for name in ("a.txt", "a.rar", "a.lzh", "a.arj", "a.gz", "zip"):
            self.assertFalse(archive.looks_like_archive(name), name)


if __name__ == "__main__":
    unittest.main()
