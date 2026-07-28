# 테스트가 함께 쓰는 도구. 공유 목록을 만들고 원격 경로를 조립한다

import tempfile
from pathlib import Path

from mdirnet import shares as shares_mod


def make_shares(*paths, config_path=None):
    """테스트용 공유 목록.

    저장 파일 경로를 임시 위치로 돌려 실제 `~/.mdirnet/shares.json` 을 절대
    건드리지 않는다. 테스트가 사용자의 설정을 망치면 안 된다.
    """
    if config_path is None:
        config_path = Path(tempfile.mkdtemp()) / "shares.json"

    shares = shares_mod.ShareSet(config_path=config_path)
    for path in paths:
        shares.add(path)
    return shares


def only_name(shares):
    """공유가 하나뿐일 때 그 이름. 원격 경로의 첫 조각으로 쓴다."""
    names = shares.names
    assert len(names) == 1, "공유가 하나가 아닙니다: %s" % names
    return names[0]


def rpath(share_name, *parts):
    """원격 경로를 만든다. 구분자는 항상 `/`."""
    return shares_mod.join(share_name, *parts)
