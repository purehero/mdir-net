# 로컬 파일시스템 조회와 원격 경로의 안전성 검증을 담당하는 모듈

import os
from pathlib import Path


class PathDenied(Exception):
    """공유 루트를 벗어난 경로 요청. 원격 요청을 거부할 때 쓴다."""


def resolve_within(root, candidate):
    """candidate 를 실제 경로로 펼친 뒤 root 안쪽인지 검사한다.

    원격에서 온 모든 경로는 예외 없이 이 함수를 통과해야 한다.
    심링크를 따라간 결과까지 검사하므로 루트 밖을 가리키는 심링크도 막힌다.
    """
    root = Path(root).expanduser().resolve()
    target = Path(candidate).expanduser()
    if not target.is_absolute():
        target = root / target

    # strict=False: 아직 없는 파일(수신 대상)도 검사할 수 있어야 한다
    target = target.resolve()

    if target != root and root not in target.parents:
        raise PathDenied("공유 범위를 벗어난 경로입니다: %s" % candidate)
    return target


def list_dir(path):
    """디렉터리 항목을 디렉터리 우선 + 이름순으로 반환한다.

    stat 이 실패하는 항목(깨진 심링크, 권한 없음)은 크기를 0 으로 두고 남긴다.
    목록 자체가 통째로 실패하는 것보다 낫다.
    """
    entries = []
    with os.scandir(path) as it:
        for item in it:
            try:
                is_dir = item.is_dir()
                stat = item.stat()
                size, mtime = stat.st_size, stat.st_mtime
            except OSError:
                is_dir, size, mtime = False, 0, 0.0

            entries.append(
                {
                    "name": item.name,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else size,
                    "mtime": mtime,
                }
            )

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def walk_tree(root):
    """root 아래 모든 파일을 (상대경로, 크기) 목록으로 반환한다.

    재귀 전송에서 전체 크기를 미리 알아 진행률을 계산하려고 파일만 모은다.
    빈 디렉터리는 목록에 없으므로 수신 측이 부모 디렉터리를 만들어 처리한다.
    읽을 수 없는 하위 디렉터리는 조용히 건너뛴다.
    """
    root = Path(root)
    files = []

    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _: None):
        dirnames.sort()
        base = Path(dirpath)
        for name in sorted(filenames):
            full = base / name
            if full.is_symlink():
                continue  # 심링크는 따라가지 않는다. 순환과 루트 탈출을 동시에 막는다
            try:
                size = full.stat().st_size
            except OSError:
                continue
            files.append({"path": full.relative_to(root).as_posix(), "size": size})

    return files


def human_size(n):
    """바이트 수를 화면에 넣기 좋은 문자열로 만든다."""
    if n < 1024:
        return "%d B" % n
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit)


def unique_part_path(dest):
    """수신용 임시 경로. 완료 후 rename 하므로 중단 시 반쪽 파일이 남지 않는다."""
    return dest.with_name(dest.name + ".part")
