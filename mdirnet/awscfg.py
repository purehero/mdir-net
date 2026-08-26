# aws CLI 가 쓰는 `~/.aws/credentials` 와 `~/.aws/config` 를 읽는 모듈
#
# aws CLI 를 부르지 않는다. 설치돼 있지 않을 수도 있고, 부르면 그 순간 이
# 프로그램이 aws CLI 에 의존하게 된다. 파일 형식은 ini 라 `configparser` 로
# 충분하다.
#
# **정적 키를 가진 프로필만 쓸 수 있다.** SSO 와 `credentials_process`, 역할
# 위임(assume role)은 토큰을 받아오는 절차가 따로 있어서 파일만 읽어서는 안 된다.
# 그런 프로필도 목록에는 내보내되 왜 못 쓰는지 적어둔다 - 목록에서 조용히
# 빼버리면 "내 프로필이 왜 없지" 가 된다.

import configparser
import os
from pathlib import Path

# 이 열쇠들이 있으면 파일만으로는 자격증명을 만들 수 없다
_NEEDS_MORE = (
    ("sso_start_url", "SSO 프로필입니다"),
    ("sso_session", "SSO 프로필입니다"),
    ("credentials_process", "외부 명령으로 키를 받는 프로필입니다"),
    ("role_arn", "역할을 위임받는 프로필입니다"),
)


def credentials_path():
    return Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or Path.home() / ".aws" / "credentials")


def config_path():
    return Path(os.environ.get("AWS_CONFIG_FILE") or Path.home() / ".aws" / "config")


def _read(path):
    """ini 파일 하나를 {섹션: {키: 값}} 으로. 없거나 깨졌으면 빈 dict.

    설정이 깨졌다고 프로그램이 멈출 이유는 없다. 프로필이 안 보이면 사용자가
    직접 입력으로 돌아가면 된다.
    """
    parser = configparser.RawConfigParser()
    try:
        parser.read_string(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, configparser.Error):
        return {}
    return {name: dict(parser[name]) for name in parser.sections()}


def _merge():
    """두 파일을 프로필 이름 기준으로 합친다.

    `config` 의 섹션은 `[profile 이름]` 이고 기본 프로필만 `[default]` 다.
    같은 키가 양쪽에 있으면 `credentials` 쪽이 이긴다 - aws CLI 와 같은 순서다.
    """
    merged = {}
    for section, values in _read(config_path()).items():
        name = section[len("profile "):] if section.startswith("profile ") else section
        merged.setdefault(name, {}).update(values)
    for name, values in _read(credentials_path()).items():
        merged.setdefault(name, {}).update(values)
    return merged


class Profile:
    """프로필 하나. 쓸 수 있는지와, 못 쓴다면 왜인지를 함께 들고 있다."""

    def __init__(self, name, values):
        self.name = name
        self.region = values.get("region", "")
        self.access_key = values.get("aws_access_key_id", "")
        self.secret_key = values.get("aws_secret_access_key", "")
        self.session_token = values.get("aws_session_token", "")
        self.reason = self._why_not(values)

    def _why_not(self, values):
        """쓸 수 없으면 그 이유. 쓸 수 있으면 빈 문자열."""
        if self.access_key and self.secret_key:
            return ""  # 정적 키가 있으면 다른 열쇠가 섞여 있어도 그것으로 붙는다
        for key, reason in _NEEDS_MORE:
            if values.get(key):
                return reason
        return "키가 없는 프로필입니다"

    @property
    def usable(self):
        return not self.reason


def profiles():
    """읽을 수 있는 프로필 전부. 기본 프로필이 맨 앞에 온다."""
    found = [Profile(name, values) for name, values in _merge().items()]
    found.sort(key=lambda p: (p.name != "default", p.name.lower()))
    return found


def get(name):
    return next((p for p in profiles() if p.name == name), None)


def preferred():
    """처음 고를 프로필 이름. AWS_PROFILE 을 따르고, 없으면 쓸 수 있는 첫 것."""
    wanted = os.environ.get("AWS_PROFILE", "")
    found = profiles()
    if wanted and any(p.name == wanted for p in found):
        return wanted
    return next((p.name for p in found if p.usable), found[0].name if found else "")


def available():
    """고를 프로필이 하나라도 있는가."""
    return bool(profiles())
