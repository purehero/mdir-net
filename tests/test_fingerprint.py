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
    def test_same_certificate_gives_the_same_value(self):
        """받는 쪽의 '내 지문' 과 거는 쪽의 '상대 지문' 이 같아야 대조가 성립한다."""
        self.assertEqual(fp.fingerprint(b"cert"), fp.fingerprint(b"cert"))

    def test_one_byte_changes_everything(self):
        """중간자는 자기 인증서를 내밀게 되고, 그러면 값이 갈려야 한다."""
        self.assertNotEqual(
            fp.fingerprint(b"\x00" * 32),
            fp.fingerprint(b"\x00" * 31 + b"\x01"),
        )

    def test_high_bytes_do_not_confuse_it(self):
        """DER 에는 0x80 이상이 흔하다. 부호 있는 바이트로 다루는 구현을 잡는다."""
        self.assertNotEqual(fp.fingerprint(bytes([0x80])), fp.fingerprint(bytes([0x00])))

    def test_empty_certificate_is_refused(self):
        """인증서를 못 받은 채 만든 값을 사용자가 확인된 것으로 믿으면 안 된다."""
        with self.assertRaises(ValueError):
            fp.fingerprint(b"")

    def test_shape(self):
        text = fp.fingerprint(b"a")

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

        벡터는 안드로이드 저장소가 갖는다. 그쪽이 곁에 없으면 건너뛴다 -
        `tools/gen-golden.py --out` 으로 그쪽 경로를 주면 다시 돌아온다.
        """
        if not GOLDEN.is_file():
            self.skipTest("골든 벡터가 없습니다 (안드로이드 저장소가 곁에 없다)")

        rows = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertTrue(rows, "벡터가 비었다")

        for row in rows:
            with self.subTest(row["note"]):
                self.assertEqual(
                    row["fingerprint"],
                    fp.fingerprint(bytes.fromhex(row["cert"])),
                    "golden/fingerprints.json 이 낡았습니다. tools/gen-golden.py 를 실행하세요",
                )


if __name__ == "__main__":
    unittest.main()
