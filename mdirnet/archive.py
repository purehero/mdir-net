# 압축 파일을 디렉터리처럼 읽는다 (MDir 에 있던 기능)
#
# 표준 라이브러리로 되는 것만 다룬다. ZIP 과 TAR 계열이다. MDir 이 다루던
# LZH, ARJ, RAR 는 외부 도구나 의존성이 필요해서 뺐다. 못 여는 것은 못 연다고
# 말하고 평소처럼 파일로 다룬다.
#
# **읽기 전용이다.** 압축 안에 쓰거나 지우지 않는다.
#
# 위험한 곳은 꺼내기 한 군데다. 압축 안의 이름은 만든 쪽이 정하는 값이라
# `../../etc/passwd` 같은 것이 들어 있을 수 있다(zip slip). 꺼낼 때 목적지
# 안쪽인지 반드시 확인한다.

import tarfile
import time
import zipfile
from pathlib import Path, PurePosixPath

#: 열어볼 확장자. 뒤에서부터 맞춰본다 (`.tar.gz` 가 `.gz` 보다 먼저다).
SUFFIXES = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tgz",
    ".tbz2",
    ".txz",
    ".zip",
    ".tar",
)


class ArchiveError(Exception):
    """열지 못했거나 깨진 압축."""


def looks_like_archive(name):
    """이름만 보고 판정한다. 실제로 열리는지는 열어봐야 안다."""
    low = str(name).lower()
    return any(low.endswith(suffix) for suffix in SUFFIXES)


def _entries(path):
    """(안쪽 경로, 디렉터리인가, 크기, mtime) 목록. 압축 형식을 여기서 흡수한다."""
    path = Path(path)
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                return [
                    (
                        info.filename,
                        info.is_dir(),
                        info.file_size,
                        _zip_mtime(info),
                    )
                    for info in zf.infolist()
                ]

        with tarfile.open(path) as tf:
            return [
                (member.name, member.isdir(), member.size, float(member.mtime))
                for member in tf.getmembers()
                if member.isdir() or member.isfile()
            ]
    except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
        raise ArchiveError(str(exc)) from exc


def _zip_mtime(info):
    """ZIP 은 도스 시각을 담는다. 값이 이상하면 0 으로 둔다."""
    try:
        return time.mktime(tuple(info.date_time) + (0, 0, -1))
    except (ValueError, OverflowError):
        return 0.0


def list_dir(path, inner=""):
    """압축 안의 **한 단계**를 목록으로. 패널이 쓰는 모양과 같다.

    압축 안의 이름은 평평한 목록이라 디렉터리가 항목으로 없을 수도 있다.
    그래서 이름을 조각내어 그 단계에 실제로 보이는 것을 직접 만든다.
    """
    prefix = _clean(inner)
    depth = len(prefix.parts)

    dirs, files = {}, {}
    for name, is_dir, size, mtime in _entries(path):
        parts = _clean(name).parts
        if _escapes(parts):
            continue  # 밖을 가리키는 항목. 몇 개인지는 escaping_count 가 센다
        if len(parts) <= depth or parts[:depth] != prefix.parts:
            continue

        head = parts[depth]
        if len(parts) > depth + 1 or is_dir:
            # 더 깊은 곳에 있는 항목이면 이 단계에는 폴더로만 보인다
            spot = dirs.setdefault(head, {"name": head, "is_dir": True, "size": 0})
            spot["mtime"] = max(spot.get("mtime", 0.0), mtime)
        else:
            files[head] = {"name": head, "is_dir": False, "size": size, "mtime": mtime}

    # 같은 이름이 폴더로도 파일로도 있으면 폴더로 본다. 들어갈 수 있어야 한다
    for name in dirs:
        files.pop(name, None)

    return list(dirs.values()) + list(files.values())


def read_head(path, inner, limit):
    """보기용으로 앞부분만. (전체 크기, 앞부분 바이트)."""
    target = str(_clean(inner))
    for name, is_dir, size, _ in _entries(path):
        if is_dir or str(_clean(name)) != target:
            continue
        with _open_member(path, name) as handle:
            return size, handle.read(limit)
    raise ArchiveError("압축 안에 없습니다: %s" % inner)


def extract(path, inner, dest):
    """압축 안의 파일 하나를 `dest` 로 꺼낸다.

    `dest` 는 이미 정해진 목적지 경로다. 압축 안의 이름을 목적지에 이어붙이는
    일은 부르는 쪽이 하고, 그때 `safe_join` 을 써야 한다.
    """
    dest = Path(dest)
    target = str(_clean(inner))
    for name, is_dir, _, _ in _entries(path):
        if is_dir or str(_clean(name)) != target:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _open_member(path, name) as handle, open(dest, "wb") as out:
            while True:
                chunk = handle.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        return
    raise ArchiveError("압축 안에 없습니다: %s" % inner)


def safe_join(root, inner):
    """`root` 안쪽으로만 이어붙인다. 밖으로 나가면 ValueError.

    압축 안의 이름은 만든 쪽이 정한다. `../../etc/passwd` 나 절대 경로가 들어
    있는 압축이 실제로 돌아다닌다(zip slip). 꺼낼 때 반드시 지나야 하는 문이다.
    """
    root = Path(root).resolve()
    target = (root / _clean(inner)).resolve()

    if target != root and root not in target.parents:
        raise ValueError("압축 안의 경로가 목적지 밖을 가리킵니다: %s" % inner)
    return target


def walk(path, inner=""):
    """`inner` 아래의 모든 파일을 (안쪽 경로, 크기) 로 펼친다. 재귀 꺼내기용."""
    prefix = _clean(inner)
    out = []
    for name, is_dir, size, _ in _entries(path):
        if is_dir:
            continue
        clean = _clean(name)
        if _escapes(clean.parts):
            continue
        if prefix.parts and clean.parts[: len(prefix.parts)] != prefix.parts:
            continue
        out.append((str(clean), size))
    return out


def escaping_count(path):
    """압축 밖을 가리키는 항목 수.

    목록에서는 감추지만 **몇 개가 감춰졌는지는 알려야 한다.** 조용히 지우면
    사용자는 압축이 온전한 줄 알고, 그런 압축을 만든 쪽의 의도도 드러나지 않는다.
    """
    try:
        return sum(1 for name, _, _, _ in _entries(path) if _escapes(_clean(name).parts))
    except ArchiveError:
        return 0


def _escapes(parts):
    """`..` 이 하나라도 있으면 밖을 가리킬 수 있다고 본다.

    `a/../b` 처럼 결과적으로는 안쪽인 것도 걸리지만, 압축 안의 이름에 `..` 을
    넣을 이유가 없다. 넓게 막고 safe_join 이 다시 한 번 본다.
    """
    return ".." in parts


def _open_member(path, name):
    path = Path(path)
    if zipfile.is_zipfile(path):
        return zipfile.ZipFile(path).open(name)

    tf = tarfile.open(path)
    handle = tf.extractfile(name)
    if handle is None:
        raise ArchiveError("읽을 수 없는 항목입니다: %s" % name)
    return handle


def _clean(name):
    """앞뒤 군더더기를 떼고 POSIX 경로로. `..` 은 여기서 지우지 않는다.

    지워버리면 `safe_join` 이 볼 것이 없어진다. 밖을 가리키는지 판정하는 일은
    그쪽에 맡기고 여기서는 모양만 고른다.
    """
    text = str(name).replace("\\", "/").strip("/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    return PurePosixPath(*parts) if parts else PurePosixPath()
