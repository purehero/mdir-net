# 시크릿을 OS 의 비밀 저장소에 맡기는 모듈 (macOS 키체인, Windows DPAPI)
#
# 왜 OS 에 맡기는가. 앱이 **아무것도 묻지 않고** 풀 수 있는 암호화는 보호가 아니다 -
# 앱이 아는 것은 그 사용자로 도는 무엇이든 알 수 있고, 푸는 방법은 소스에 적혀 있다.
# 기계 식별자로 키를 만들어 파일에 넣는 방식이 흔하지만 그것은 난독화이지 암호화가
# 아니다. AWS 시크릿 키는 새면 과금과 데이터가 함께 걸리는 자격증명이라, "암호화해
# 뒀다" 는 안심이 실제 보호보다 나쁘게 작용한다.
#
# 그래서 복호화 열쇠가 **사용자의 로그인 자격증명에서 나오는** 수단만 쓴다. 그런 것이
# 없는 환경(주로 리눅스)에서는 저장하지 않고 매번 입력받는다. 물러서지 않는다.

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .shares import default_config_path

# 저장소에서 이 이름으로 묶인다. 키체인 앱에서도 이 이름으로 보인다
SERVICE = "MDirNet S3"

# 계정 이름으로 쓸 수 있는 글자. 액세스 키 ID 는 영숫자지만, 넘겨받은 것을
# 그대로 명령 줄에 넣기 전에 반드시 확인한다
_SAFE_ACCOUNT = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")

# 저장하는 값은 늘 base64 다. `security` 가 비ASCII 값을 hex 로 뱉는 바람에
# 읽을 때 그것이 hex 인지 값 자체인지 구별할 수 없는 경우가 생긴다. 감싸두면
# 따옴표든 백슬래시든 한글이든 그대로 왕복한다
_SAFE_BLOB = re.compile(r"^[A-Za-z0-9+/=]*$")


class KeystoreError(Exception):
    """비밀 저장소에 맡기거나 꺼내오지 못했다.

    이 오류로 연결이 막히지는 않는다. 최악의 경우 사용자가 키를 다시 치면 된다.
    """


def _pack(secret):
    return base64.b64encode(secret.encode("utf-8")).decode("ascii")


def _unpack(blob):
    try:
        return base64.b64decode(blob.encode("ascii"), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise KeystoreError("저장된 값을 읽지 못했습니다: %s" % exc)


def _check_account(account):
    if not _SAFE_ACCOUNT.match(account or ""):
        raise KeystoreError("저장할 수 없는 이름입니다: %r" % account)
    return account


# --- macOS 키체인 ---


class Keychain:
    """`/usr/bin/security` 를 거쳐 로그인 키체인에 맡긴다.

    복호화 열쇠는 로그인 키체인이 쥐고 있고, 그것은 사용자의 로그인 암호로
    보호된다. 다른 사용자도, 다른 기계로 파일을 옮겨간 사람도 풀 수 없다.
    """

    label = "키체인"
    TOOL = "/usr/bin/security"
    NOT_FOUND = 44  # 항목이 없을 때의 종료 코드

    def __init__(self, keychain=""):
        # 테스트가 임시 키체인을 물린다. 비우면 기본(로그인) 키체인이다
        self._keychain = str(keychain or "")

    def _tail(self):
        return [self._keychain] if self._keychain else []

    def save(self, account, secret):
        blob = _pack(secret)
        if not _SAFE_BLOB.match(blob):  # base64 는 늘 통과한다. 그래도 확인한다
            raise KeystoreError("저장할 수 없는 값입니다")

        # 시크릿을 argv 에 두면 ps 에 보인다. `-i` 는 명령 줄 자체를 stdin 으로
        # 받으므로 argv 에는 `security -i` 만 남는다
        line = 'add-generic-password -a "%s" -s "%s" -w "%s" -U %s\n' % (
            _check_account(account), SERVICE, blob,
            '"%s"' % self._keychain if self._keychain else "",
        )
        done = self._run([self.TOOL, "-i"], text=line)
        if done.returncode != 0:
            raise KeystoreError(_first_line(done.stderr) or "키체인에 넣지 못했습니다")

    def load(self, account):
        done = self._run(
            [self.TOOL, "find-generic-password", "-a", _check_account(account),
             "-s", SERVICE, "-w"] + self._tail()
        )
        if done.returncode == self.NOT_FOUND:
            return None
        if done.returncode != 0:
            raise KeystoreError(_first_line(done.stderr) or "키체인에서 읽지 못했습니다")
        return _unpack(done.stdout.strip())

    def delete(self, account):
        done = self._run(
            [self.TOOL, "delete-generic-password", "-a", _check_account(account),
             "-s", SERVICE] + self._tail()
        )
        # 없는 것을 지우라는 것은 이미 원하는 상태다. 오류로 다루지 않는다
        if done.returncode not in (0, self.NOT_FOUND):
            raise KeystoreError(_first_line(done.stderr) or "키체인에서 지우지 못했습니다")

    @staticmethod
    def _run(args, text=None):
        try:
            return subprocess.run(
                args, input=text, capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeystoreError("키체인을 부르지 못했습니다: %s" % exc)


# --- Windows DPAPI ---


class Dpapi:
    """Windows 의 자격증명 보호(DPAPI)로 암호화해 파일에 둔다.

    DPAPI 는 저장소가 아니라 암복호화만 해주므로 결과 덩어리는 우리가 보관한다.
    열쇠는 **사용자 계정**에 묶여 있어, 파일을 통째로 옮겨가도 다른 사용자나
    다른 기계에서는 풀리지 않는다.
    """

    label = "Windows 자격증명 보호"
    FILE = "s3-secrets.json"

    def __init__(self, path=None):
        self._path = Path(path) if path else default_config_path().with_name(self.FILE)

    # -- 파일 --

    def _read_all(self):
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write_all(self, data):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_suffix(".tmp")
            temp.write_text(json.dumps(data) + "\n", encoding="utf-8")
            os.replace(temp, self._path)
        except OSError as exc:
            raise KeystoreError("저장하지 못했습니다: %s" % exc)

    # -- 저장소 인터페이스 --

    def save(self, account, secret):
        data = self._read_all()
        data[_check_account(account)] = base64.b64encode(
            self._protect(_pack(secret).encode("ascii"))
        ).decode("ascii")
        self._write_all(data)

    def load(self, account):
        blob = self._read_all().get(_check_account(account))
        if not isinstance(blob, str) or not blob:
            return None
        try:
            packed = self._unprotect(base64.b64decode(blob.encode("ascii")))
        except (ValueError, KeystoreError):
            # 다른 사용자의 것이거나 깨졌다. 없는 것으로 다룬다 - 치면 된다
            return None
        return _unpack(packed.decode("ascii"))

    def delete(self, account):
        data = self._read_all()
        if data.pop(_check_account(account), None) is None:
            return
        self._write_all(data)

    # -- DPAPI --

    @staticmethod
    def _blob_type():
        import ctypes

        class Blob(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        return ctypes, Blob

    @classmethod
    def _call(cls, name, data):
        ctypes, Blob = cls._blob_type()

        source = ctypes.create_string_buffer(data, len(data))
        given = Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
        got = Blob()

        crypt32 = ctypes.windll.crypt32
        ok = getattr(crypt32, name)(
            ctypes.byref(given), None, None, None, None, 0, ctypes.byref(got)
        )
        if not ok:
            raise KeystoreError("%s 실패 (오류 %d)" % (name, ctypes.GetLastError()))
        try:
            return ctypes.string_at(got.pbData, got.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(got.pbData)

    @classmethod
    def _protect(cls, data):
        return cls._call("CryptProtectData", data)

    @classmethod
    def _unprotect(cls, data):
        return cls._call("CryptUnprotectData", data)


# --- 고르기 ---


def _first_line(text):
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


def _pick():
    """이 기계에서 쓸 수 있는 저장소. 없으면 None."""
    if sys.platform == "darwin" and os.path.exists(Keychain.TOOL):
        return Keychain()
    if sys.platform == "win32":
        return Dpapi()
    # 리눅스의 GNOME 키링(secret-tool)은 데스크톱 환경에 따라 없는 경우가 흔하고,
    # 헤드리스에서는 아예 못 쓴다. 있는 척하다 실패하는 것보다 없다고 말한다
    return None


_store = None
_picked = False


def store():
    """이 기계의 비밀 저장소. 한 번만 고른다."""
    global _store, _picked
    if not _picked:
        _store, _picked = _pick(), True
    return _store


def available():
    return store() is not None


def label():
    """화면에 보여줄 저장소 이름."""
    return store().label if available() else ""


def save(account, secret):
    if not available():
        raise KeystoreError("이 환경에는 쓸 수 있는 비밀 저장소가 없습니다")
    store().save(account, secret)


def load(account):
    """맡겨둔 시크릿. 없으면 None."""
    return store().load(account) if available() else None


def delete(account):
    if available():
        store().delete(account)
