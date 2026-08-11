# 색상셋처럼 화면에만 관계된 취향을 ~/.mdirnet 에 저장하고 읽는 모듈

import json
import os

from .shares import default_config_path

# 공유 목록(shares.json) 옆에 둔다. 같은 폴더를 쓰지만 성격이 달라 파일은 나눈다
_FILE = "settings.json"


def _path():
    return default_config_path().with_name(_FILE)


def load_theme(default="black"):
    """저장된 색상셋 이름. 파일이 없거나 깨졌으면 기본값을 준다."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    name = raw.get("theme") if isinstance(raw, dict) else None
    return name if isinstance(name, str) and name else default


def save_theme(name):
    """색상셋 이름을 저장한다. 실패는 부르는 쪽이 처리한다."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 임시 파일에 쓰고 바꿔치운다. 쓰다 죽어도 기존 값이 남는다 (shares 와 같다)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"theme": name}) + "\n", encoding="utf-8")
    os.replace(temp, path)
