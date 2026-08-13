# 연결 지문 — 사람이 대조하는 값이라 규칙이 흔들리면 안 된다

import json
import unittest
from pathlib import Path

from mdirnet import fingerprint as fp

GOLDEN = (
    Path(__file__).resolve().parent.parent
    / "android"
    / "app"
    / "src"
    / "test"
    / "resources"
    / "golden"
    / "fingerprints.json"
)


class TestFingerprint(unittest.TestCase):
    def test_order_does_not_matter(self):
        """누가 걸었든 양쪽 화면에 같은 값이 떠야 한다."""
        self.assertEqual(fp.fingerprint(b"a", b"b"), fp.fingerprint(b"b", b"a"))

    def test_both_certificates_go_in(self):
        """한쪽만 쓰면 중간자가 양쪽에 다른 채널을 열어도 값이 같아질 수 있다."""
        self.assertNotEqual(fp.fingerprint(b"a", b"b"), fp.fingerprint(b"a", b"c"))
        self.assertNotEqual(fp.fingerprint(b"a", b"c"), fp.fingerprint(b"z", b"c"))

    def test_one_byte_changes_everything(self):
        self.assertNotEqual(
            fp.fingerprint(b"cert", b"\x00" * 32),
            fp.fingerprint(b"cert", b"\x00" * 31 + b"\x01"),
        )

    def test_empty_certificate_is_refused(self):
        """상대 인증서를 못 받은 채 만든 값을 사용자가 확인된 것으로 믿으면 안 된다."""
        for a, b in ((b"", b"x"), (b"x", b""), (b"", b"")):
            with self.subTest(a=a, b=b):
                with self.assertRaises(ValueError):
                    fp.fingerprint(a, b)

    def test_shape(self):
        text = fp.fingerprint(b"a", b"b")

        self.assertEqual(len(text), fp.LENGTH * 2 + 2)
        groups = text.split("-")
        self.assertEqual(len(groups), fp.LENGTH * 2 // fp.GROUP)
        for group in groups:
            self.assertEqual(len(group), fp.GROUP)
            self.assertTrue(all(c in "0123456789ABCDEF" for c in group), text)

    def test_label_is_versioned(self):
        """규칙이 바뀌면 라벨 뒤 숫자를 올려야 옛 지문과 섞이지 않는다."""
        self.assertTrue(fp.LABEL.endswith(b"-v1"), fp.LABEL)

    def test_golden_file_is_current(self):
        """구현을 고치고 tools/gen-golden.py 를 잊는 것을 잡는다.

        이것이 없으면 Kotlin 쪽만 깨져서, 파이썬만 돌려본 사람은 멀쩡한 줄 안다.
        """
        rows = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertTrue(rows, "벡터가 비었다")

        for row in rows:
            with self.subTest(row["note"]):
                self.assertEqual(
                    row["fingerprint"],
                    fp.fingerprint(bytes.fromhex(row["certA"]), bytes.fromhex(row["certB"])),
                    "golden/fingerprints.json 이 낡았습니다. tools/gen-golden.py 를 실행하세요",
                )


if __name__ == "__main__":
    unittest.main()
