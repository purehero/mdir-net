# 시크릿을 OS 비밀 저장소에 맡기는 부분의 테스트
#
# macOS 쪽은 **임시 키체인**을 만들어 쓴다. 사용자의 로그인 키체인을 건드리면
# 테스트가 사람의 자격증명 사이에 항목을 남기고, 잠겨 있으면 암호를 묻는 창까지
# 띄운다. 테스트가 그런 일을 하면 안 된다.

import base64
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from mdirnet import keystore

MAC = sys.platform == "darwin" and Path(keystore.Keychain.TOOL).exists()


class TestPacking(unittest.TestCase):
    """저장하는 값은 늘 base64 다.

    `security` 는 비ASCII 값을 hex 로 뱉는다. 그러면 읽을 때 그것이 hex 인지
    값 자체인지 구별할 수 없다. 감싸두면 그 모호함이 아예 없어진다.
    """

    def round_trip(self, text):
        self.assertEqual(keystore._unpack(keystore._pack(text)), text)

    def test_an_ordinary_aws_secret_survives(self):
        self.round_trip("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")

    def test_quotes_and_backslashes_survive(self):
        """명령 줄에 넣는 값이라 이것이 새면 인용이 깨진다."""
        self.round_trip('따옴표 " 백슬래시 \\ 달러 $ 개행 없음')

    def test_non_ascii_survives(self):
        self.round_trip("한글 시크릿 키")

    def test_the_packed_form_is_always_command_line_safe(self):
        for text in ('a"b', "c\\d", "한글", "e f", "$(echo)"):
            self.assertRegex(keystore._pack(text), keystore._SAFE_BLOB)

    def test_a_broken_value_is_reported_not_returned(self):
        with self.assertRaises(keystore.KeystoreError):
            keystore._unpack("이건 base64 가 아니다")


class TestAccountNames(unittest.TestCase):
    """계정 이름은 명령 줄에 그대로 들어간다. 넣기 전에 반드시 확인한다."""

    def test_an_access_key_id_passes(self):
        self.assertEqual(keystore._check_account("AKIAIOSFODNN7EXAMPLE"),
                         "AKIAIOSFODNN7EXAMPLE")

    def test_quotes_and_spaces_are_refused(self):
        for bad in ('AKIA"x', "AKIA x", "AKIA\nx", "AKIA;rm -rf /", "", "A" * 200):
            with self.assertRaises(keystore.KeystoreError, msg=bad):
                keystore._check_account(bad)


@unittest.skipUnless(MAC, "macOS 키체인이 있는 곳에서만 돈다")
class TestKeychain(unittest.TestCase):
    """진짜 `security` 를 부르되, 임시 키체인 안에서만 논다."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "test.keychain"
        run = subprocess.run(
            ["/usr/bin/security", "create-keychain", "-p", "테스트암호", str(self.path)],
            capture_output=True, text=True,
        )
        if run.returncode != 0:
            self.skipTest("임시 키체인을 만들지 못했습니다: %s" % run.stderr.strip())
        self.addCleanup(
            subprocess.run,
            ["/usr/bin/security", "delete-keychain", str(self.path)],
            capture_output=True,
        )
        subprocess.run(
            ["/usr/bin/security", "unlock-keychain", "-p", "테스트암호", str(self.path)],
            capture_output=True,
        )
        self.store = keystore.Keychain(self.path)
        self.account = "AKIATEST" + uuid.uuid4().hex[:12].upper()

    def test_what_goes_in_comes_back_out(self):
        self.store.save(self.account, "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        self.assertEqual(
            self.store.load(self.account), "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        )

    def test_a_secret_full_of_awkward_characters_survives(self):
        awkward = '따옴표 " 백슬래시 \\ 한글 $(echo 위험) +/=='
        self.store.save(self.account, awkward)
        self.assertEqual(self.store.load(self.account), awkward)

    def test_nothing_stored_reads_as_none(self):
        self.assertIsNone(self.store.load(self.account))

    def test_saving_again_replaces_it(self):
        self.store.save(self.account, "처음")
        self.store.save(self.account, "나중")
        self.assertEqual(self.store.load(self.account), "나중")

    def test_deleting_makes_it_gone(self):
        self.store.save(self.account, "지울 것")
        self.store.delete(self.account)
        self.assertIsNone(self.store.load(self.account))

    def test_deleting_what_is_not_there_is_not_an_error(self):
        """없는 것을 지우라는 것은 이미 원하는 상태다."""
        self.store.delete(self.account)

    def test_the_secret_never_reaches_the_command_line(self):
        """argv 에 두면 ps 로 다른 사용자에게 보인다. -i 로 stdin 에 실어 보낸다."""
        seen = {}
        # 클래스에서 읽으면 staticmethod 래퍼가 벗겨진 맨 함수가 나온다. 그대로
        # 되돌리면 일반 메서드가 되어 self 가 첫 인자로 끼어든다. 감싸서 돌려놓는다
        original = keystore.Keychain.__dict__["_run"].__func__

        def spy(args, text=None):
            seen["args"] = list(args)
            seen["stdin"] = text
            return original(args, text)

        keystore.Keychain._run = staticmethod(spy)
        self.addCleanup(
            lambda: setattr(keystore.Keychain, "_run", staticmethod(original))
        )

        self.store.save(self.account, "wJalrXUtnFEMI-비밀-KEY")

        self.assertEqual(seen["args"], ["/usr/bin/security", "-i"])
        self.assertIn(keystore._pack("wJalrXUtnFEMI-비밀-KEY"), seen["stdin"])
        self.assertNotIn("비밀", " ".join(seen["args"]))


class FakeStore:
    """저장소가 있는 것처럼 굴지만 메모리에만 둔다."""

    label = "테스트 저장소"

    def __init__(self):
        self.items = {}
        self.failing = False

    def save(self, account, secret):
        if self.failing:
            raise keystore.KeystoreError("저장소가 잠겨 있습니다")
        self.items[account] = secret

    def load(self, account):
        if self.failing:
            raise keystore.KeystoreError("저장소가 잠겨 있습니다")
        return self.items.get(account)

    def delete(self, account):
        self.items.pop(account, None)


def _swap(case, chosen):
    """모듈 차원의 저장소를 갈아끼운다. 되돌리는 것까지 등록한다."""
    before = (keystore._store, keystore._picked)

    def restore():
        keystore._store, keystore._picked = before

    keystore._store, keystore._picked = chosen, True
    case.addCleanup(restore)
    return chosen


def use_fake(case):
    """가짜 저장소가 있는 것처럼."""
    return _swap(case, FakeStore())


def use_no_store(case):
    """쓸 수 있는 저장소가 없는 환경처럼."""
    _swap(case, None)


class TestModuleFront(unittest.TestCase):
    def test_it_reports_what_this_machine_has(self):
        use_fake(self)
        self.assertTrue(keystore.available())
        self.assertEqual(keystore.label(), "테스트 저장소")

    def test_with_no_store_it_says_so(self):
        use_no_store(self)
        self.assertFalse(keystore.available())
        self.assertEqual(keystore.label(), "")

    def test_without_a_store_nothing_is_kept(self):
        use_no_store(self)
        self.assertIsNone(keystore.load("AKIA"))
        keystore.delete("AKIA")  # 조용히 넘어간다
        with self.assertRaises(keystore.KeystoreError):
            keystore.save("AKIA", "비밀")

    def test_the_store_is_chosen_once(self):
        """매번 고르면 요청마다 파일 존재 확인이 붙는다."""
        _swap(self, None)
        keystore._store, keystore._picked = None, False
        self.assertIs(keystore.store(), keystore.store())


@unittest.skipUnless(sys.platform != "win32", "Windows 에서는 진짜 DPAPI 가 돈다")
class TestDpapiFileHandling(unittest.TestCase):
    """DPAPI 호출은 Windows 에서만 되지만, 파일을 다루는 부분은 어디서나 확인한다.

    암복호화를 가짜로 바꾸고 파일 읽기/쓰기/지우기만 본다.
    """

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "s3-secrets.json"
        self.store = keystore.Dpapi(self.path)
        self.store._protect = lambda data: b"ENC" + data
        self.store._unprotect = lambda data: data[3:]

    def test_what_goes_in_comes_back_out(self):
        self.store.save("AKIA1", "비밀 하나")
        self.assertEqual(self.store.load("AKIA1"), "비밀 하나")

    def test_the_plain_secret_is_not_in_the_file(self):
        self.store.save("AKIA1", "wJalrXUtnFEMI")
        self.assertNotIn("wJalrXUtnFEMI", self.path.read_text(encoding="utf-8"))

    def test_more_than_one_account_can_be_kept(self):
        self.store.save("AKIA1", "하나")
        self.store.save("AKIA2", "둘")
        self.assertEqual(self.store.load("AKIA1"), "하나")
        self.assertEqual(self.store.load("AKIA2"), "둘")

    def test_deleting_one_leaves_the_other(self):
        self.store.save("AKIA1", "하나")
        self.store.save("AKIA2", "둘")
        self.store.delete("AKIA1")
        self.assertIsNone(self.store.load("AKIA1"))
        self.assertEqual(self.store.load("AKIA2"), "둘")

    def test_a_blob_this_user_cannot_open_reads_as_nothing(self):
        """다른 사용자의 것이면 DPAPI 가 거부한다. 없는 것으로 다룬다 - 치면 된다."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"AKIA1": base64.b64encode("남의 것".encode()).decode("ascii")}),
            encoding="utf-8",
        )

        def refuse(data):
            raise keystore.KeystoreError("CryptUnprotectData 실패")

        self.store._unprotect = refuse
        self.assertIsNone(self.store.load("AKIA1"))

    def test_a_missing_file_reads_as_nothing(self):
        self.assertIsNone(self.store.load("AKIA1"))

    def test_a_broken_file_reads_as_nothing(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("이건 json 이 아니다", encoding="utf-8")
        self.assertIsNone(self.store.load("AKIA1"))


if __name__ == "__main__":
    unittest.main()
