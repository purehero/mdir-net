# 상대에게 열어줄 공유 폴더 목록을 관리하고 원격 경로를 실제 경로로 바꾸는 모듈

import json
import os
from pathlib import Path, PurePosixPath

from . import fsops

# 상대와 주고받는 경로에서 가상 루트를 가리키는 값
ROOT = "."

_CONFIG_DIR = ".mdirnet"
_CONFIG_FILE = "shares.json"


class Share:
    """공유 폴더 하나. name 은 상대에게 보이는 이름, path 는 내 실제 경로."""

    def __init__(self, name, path):
        self.name = name
        self.path = Path(path)

    @property
    def exists(self):
        """외장 디스크를 빼면 없어질 수 있다. 매번 확인한다."""
        return self.path.is_dir()

    def as_dict(self):
        return {"name": self.name, "path": str(self.path)}


class ShareSet:
    """공유 폴더 목록. 원격 경로를 실제 경로로 바꾸는 유일한 관문이다.

    상대와 주고받는 경로는 호스트 절대 경로가 아니라 **공유 상대 경로**다.
        "."                     가상 루트 (공유 목록)
        "Downloads"             공유 폴더 하나
        "Downloads/sub/a.txt"   그 안의 파일
    호스트 경로가 새어나가지 않고, Windows 경로를 macOS 가 다룰 필요도 없다.
    """

    def __init__(self, shares=None, config_path=None):
        self._shares = list(shares or [])
        self._config_path = Path(config_path) if config_path else default_config_path()

    # --- 목록 다루기 ---

    def __len__(self):
        return len(self._shares)

    def __iter__(self):
        return iter(self._shares)

    @property
    def names(self):
        return [s.name for s in self._shares]

    def get(self, name):
        return next((s for s in self._shares if s.name == name), None)

    def add(self, path):
        """폴더를 목록에 넣고 Share 를 돌려준다. 이미 있으면 그것을 돌려준다."""
        path = Path(path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("폴더가 아닙니다: %s" % path)

        existing = next((s for s in self._shares if s.path == path), None)
        if existing:
            return existing

        share = Share(self._unique_name(path), path)
        self._shares.append(share)
        return share

    def remove(self, name):
        before = len(self._shares)
        self._shares = [s for s in self._shares if s.name != name]
        return len(self._shares) != before

    def _unique_name(self, path):
        """폴더 이름을 그대로 쓰고, 겹치면 뒤에 숫자를 붙인다.

        `/` 가 들어가면 경로 조각과 헷갈리므로 밀어낸다. 드라이브 루트처럼
        이름이 비는 경우도 있어 그때는 대체 이름을 만든다.
        """
        base = path.name or str(path).replace(os.sep, "").replace(":", "") or "공유"
        base = base.replace("/", "_").replace("\\", "_")

        taken = set(self.names)
        if base not in taken:
            return base
        for suffix in range(2, 1000):
            candidate = "%s (%d)" % (base, suffix)
            if candidate not in taken:
                return candidate
        raise ValueError("이름을 정할 수 없습니다")

    # --- 원격 경로 해석 ---

    def is_root(self, remote_path):
        return _split(remote_path) == []

    def resolve(self, remote_path):
        """원격이 보낸 상대 경로를 내 실제 경로로 바꾼다.

        모든 원격 요청은 예외 없이 이 함수를 지나야 한다. 목록에 없는 이름,
        `..` 로 공유 폴더를 벗어나는 시도, 밖을 가리키는 심링크를 모두 막는다.
        """
        parts = _split(remote_path)
        if not parts:
            raise fsops.PathDenied("가상 루트는 파일이 아닙니다")

        share = self.get(parts[0])
        if share is None:
            raise fsops.PathDenied("공유 목록에 없습니다: %s" % parts[0])

        # 공유 폴더 안쪽인지는 기존 관문이 판정한다 (심링크까지 따라가 검사)
        return fsops.resolve_within(share.path, share.path.joinpath(*parts[1:]))

    def share_of(self, remote_path):
        """이 경로가 속한 Share. 가상 루트면 None."""
        parts = _split(remote_path)
        return self.get(parts[0]) if parts else None

    def root_entries(self):
        """가상 루트의 목록. 공유 폴더들을 디렉터리처럼 보여준다.

        없어진 폴더(외장 디스크를 뺀 경우)는 내보내지 않는다. 상대가 들어가도
        실패할 항목을 보여줄 이유가 없다.
        """
        entries = []
        for share in self._shares:
            if not share.exists:
                continue
            try:
                mtime = share.path.stat().st_mtime
            except OSError:
                mtime = 0.0
            entries.append({"name": share.name, "is_dir": True, "size": 0, "mtime": mtime})
        return sorted(entries, key=lambda e: e["name"].lower())

    # --- 저장 / 불러오기 ---

    @property
    def config_path(self):
        return self._config_path

    def save(self):
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [s.as_dict() for s in self._shares], ensure_ascii=False, indent=2
        )
        # 임시 파일에 쓰고 바꿔치운다. 쓰다 죽어도 기존 목록이 남는다
        temp = self._config_path.with_suffix(".tmp")
        temp.write_text(payload + "\n", encoding="utf-8")
        os.replace(temp, self._config_path)

    def load(self):
        """저장된 목록을 읽는다. 파일이 없거나 깨졌으면 빈 목록으로 시작한다."""
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self
        if not isinstance(raw, list):
            return self

        self._shares = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name, path = item.get("name"), item.get("path")
            if not isinstance(name, str) or not isinstance(path, str) or not name:
                continue
            if "/" in name or "\\" in name:
                continue  # 경로 조각과 헷갈리는 이름은 버린다
            if name in self.names:
                continue
            self._shares.append(Share(name, path))
        return self


def default_config_path():
    return Path.home() / _CONFIG_DIR / _CONFIG_FILE


def join(*parts):
    """원격 경로를 만든다. 구분자는 항상 `/` 다."""
    cleaned = [str(p) for p in parts if p not in (None, "", ROOT)]
    return "/".join(cleaned) if cleaned else ROOT


def parent(remote_path):
    """한 단계 위 원격 경로. 가상 루트에서는 더 올라가지 않는다."""
    parts = _split(remote_path)
    return join(*parts[:-1]) if parts else ROOT


def normalize(remote_path):
    """원격 경로를 표준 형태로 다듬는다. `..` 과 빈 조각을 없앤 결과."""
    return join(*_split(remote_path))


def display(remote_path):
    """화면에 보여줄 형태. 가상 루트는 `/` 로 표시한다."""
    parts = _split(remote_path)
    return "/" + "/".join(parts) if parts else "/"


def _split(remote_path):
    """원격 경로를 조각으로 나누고 `..` 을 제대로 처리한다.

    `..` 을 그냥 버리면 `공유/a/../b` 가 `공유/a/b` 로 해석되어 사용자 의도와
    달라진다. 앞 조각을 지우는 방식으로 처리하되 **빈 목록보다 아래로는 내려가지
    않는다.** 그래서 아무리 `..` 을 쌓아도 최대 가상 루트까지만 올라간다.
    """
    text = str(remote_path or "").replace("\\", "/")

    parts = []
    for piece in PurePosixPath(text).parts:
        if piece in ("", ".", "/"):
            continue
        if piece == "..":
            if parts:
                parts.pop()
            continue  # 이미 최상위면 그대로 머문다
        parts.append(piece)
    return parts
