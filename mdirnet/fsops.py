# 로컬 파일시스템 조회와 원격 경로의 안전성 검증을 담당하는 모듈

import os
import subprocess
import time
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

    return sort_entries(entries, SORT_NAME)


# 정렬 기준. MDir 처럼 키 하나로 돌려가며 쓴다
SORT_NAME, SORT_EXT, SORT_SIZE, SORT_DATE = "name", "ext", "size", "date"
SORT_ORDER = (SORT_NAME, SORT_EXT, SORT_SIZE, SORT_DATE)
SORT_LABELS = {SORT_NAME: "이름", SORT_EXT: "확장자", SORT_SIZE: "크기", SORT_DATE: "날짜"}


def sort_entries(entries, mode):
    """항목을 정렬한다. 기준이 무엇이든 디렉터리는 늘 위로 모은다.

    원격에서 받은 목록에도 그대로 쓸 수 있게 순수 함수로 둔다. 정렬을 프로토콜에
    끼워넣지 않아도 되는 이유다.
    """
    if mode == SORT_EXT:
        key = lambda e: (not e["is_dir"], _suffix(e["name"]), e["name"].lower())
    elif mode == SORT_SIZE:
        # 큰 파일부터. 무엇이 자리를 차지하는지 찾는 게 목적이다
        key = lambda e: (not e["is_dir"], -e["size"], e["name"].lower())
    elif mode == SORT_DATE:
        key = lambda e: (not e["is_dir"], -e["mtime"], e["name"].lower())
    else:
        key = lambda e: (not e["is_dir"], e["name"].lower())

    return sorted(entries, key=key)


def _suffix(name):
    """확장자만 소문자로. 숨김 파일(.gitignore)은 확장자가 없는 것으로 본다."""
    base = name[1:] if name.startswith(".") else name
    _, dot, ext = base.rpartition(".")
    return ext.lower() if dot else ""


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


def mdir_size(n):
    """MDir 처럼 천 단위로 끊은 크기. `15,210` 형태."""
    return format(int(n), ",d")


def mdir_date(mtime):
    """MDir 처럼 `93-12-01` (YY-MM-DD)."""
    if not mtime:
        return ""
    return time.strftime("%y-%m-%d", time.localtime(mtime))


def mdir_time(mtime):
    """MDir 처럼 `2:11a` (12시간제 + a/p)."""
    if not mtime:
        return ""
    stamp = time.localtime(mtime)
    hour = stamp.tm_hour % 12 or 12
    return "%d:%02d%s" % (hour, stamp.tm_min, "a" if stamp.tm_hour < 12 else "p")


def split_name(name):
    """이름과 확장자를 나눈다. MDir 은 두 칸으로 나눠 보여줬다.

    숨김 파일(`.gitignore`)은 확장자가 없는 것으로 본다. 디렉터리는 나누지 않는다.
    """
    if name.startswith("."):
        return name, ""
    base, dot, ext = name.rpartition(".")
    return (base, ext) if dot and base else (name, "")


def walk_dirs(root):
    """root 아래 모든 하위 디렉터리의 상대 경로.

    파일이 하나도 없는 디렉터리는 walk_tree 에 잡히지 않는다. 그런 폴더도
    복사되어야 하므로 따로 모은다. 심링크는 따라가지 않는다.
    """
    root = Path(root)
    out = []
    for dirpath, dirnames, _ in os.walk(root, onerror=lambda _: None):
        dirnames[:] = sorted(d for d in dirnames if not (Path(dirpath) / d).is_symlink())
        for name in dirnames:
            out.append((Path(dirpath) / name).relative_to(root).as_posix())
    return sorted(out)


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


# --- 드라이브 / 볼륨 ---
#
# Windows 에서는 C:\ 의 부모가 자기 자신이라 '..' 로는 D: 에 갈 수 없다.
# 드라이브 목록을 따로 보여주지 않으면 다른 드라이브를 아예 쓸 수 없다.
# POSIX 는 '/' 하나로 이어져 있지만, 꽂아둔 외장 볼륨으로 한 번에 뛰는 용도로
# 같은 화면을 쓴다.

_WIN_DRIVE_KINDS = {
    2: "이동식",
    3: "고정",
    4: "네트워크",
    5: "CD/DVD",
    6: "램디스크",
}

_POSIX_VOLUME_DIRS = ("/Volumes", "/media", "/mnt", "/run/media")


def list_drives():
    """고를 수 있는 최상위 위치 목록. [{path, label, kind}] 형태."""
    return _windows_drives() if os.name == "nt" else _posix_volumes()


def _windows_drive_bits():
    """GetLogicalDrives 비트마스크. 실패하면 None.

    글자마다 exists() 를 부르면 빈 CD 드라이브에서 몇 초씩 멈추는 일이 있어
    비트마스크를 먼저 시도한다.
    """
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetLogicalDrives())
    except (ImportError, AttributeError, OSError):
        return None


def _windows_drive_kind(root):
    try:
        import ctypes

        code = int(ctypes.windll.kernel32.GetDriveTypeW(str(root)))
    except (ImportError, AttributeError, OSError, ValueError):
        return ""
    return _WIN_DRIVE_KINDS.get(code, "")


def _windows_drives():
    bits = _windows_drive_bits()
    out = []

    for index in range(26):
        letter = chr(ord("A") + index)
        root = "%s:\\" % letter

        if bits is not None:
            if not (bits >> index) & 1:
                continue
        elif not Path(root).exists():  # 비트마스크를 못 얻었을 때의 대비책
            continue

        out.append({"path": root, "label": "%s:" % letter, "kind": _windows_drive_kind(root)})
    return out


def _posix_volumes():
    out = [{"path": "/", "label": "/", "kind": "루트"}]
    seen = {"/"}

    for base in _POSIX_VOLUME_DIRS:
        base_path = Path(base)
        if not base_path.is_dir():
            continue
        try:
            children = sorted(base_path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue

        for child in children:
            text = str(child)
            if text in seen or not child.is_dir():
                continue
            seen.add(text)
            out.append({"path": text, "label": child.name, "kind": "볼륨"})

    return out


def is_filesystem_root(path):
    """더 위로 올라갈 수 없는 지점인가. Windows 의 `C:\\` 와 POSIX 의 `/`."""
    path = Path(path)
    return path == path.parent


def copy_file(src, dest, on_progress=None, chunk=256 * 1024):
    """내 PC 안에서 파일을 복사한다. 전송과 같은 규칙을 지킨다.

    `.part` 로 쓰고 완료 후 rename 하므로 중간에 끊겨도 반쪽 파일이 남지 않는다.
    """
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = unique_part_path(dest)

    copied = 0
    try:
        with open(src, "rb") as reader, open(part, "wb") as writer:
            while True:
                block = reader.read(chunk)
                if not block:
                    break
                writer.write(block)
                copied += len(block)
                if on_progress:
                    on_progress(len(block))
        os.replace(part, dest)
        return copied
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def read_head(path, limit):
    """파일 앞부분만 읽는다. 내장 뷰어가 큰 파일에 목메지 않게 한다."""
    with open(path, "rb") as fp:
        return fp.read(limit)


def is_executable(path):
    """실행할 수 있는 파일인가.

    POSIX 는 실행 권한(x)이 기준이다. Windows 에는 그 개념이 없어 `PATHEXT`
    확장자로 본다. 디렉터리는 (macOS 의 `.app` 처럼) 실행 대상이 아니다.
    """
    path = Path(path)
    if not path.is_file():
        return False

    if os.name == "nt":
        exts = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        return path.suffix.upper() in {e.strip().upper() for e in exts if e.strip()}

    return os.access(path, os.X_OK)


_launched = []  # 끝난 자식을 거두기 위해 들고 있는 핸들


def launch(path):
    """파일을 백그라운드에서 실행한다.

    입출력을 모두 버린다. 자식이 우리 터미널에 글자를 쓰면 화면이 뭉개진다.
    `start_new_session` 으로 부모와 떼어놓아 MDirNet 이 받는 Ctrl-C 나 종료가
    자식까지 끌고 가지 않게 한다.

    핸들을 모아두고 다음 실행 때 `poll()` 로 거둔다. 놓아버리면 끝난 자식이
    좀비로 남고, GC 가 Popen 을 거둘 때 ResourceWarning 도 뜬다.
    """
    path = Path(path)
    kwargs = {"start_new_session": True} if os.name != "nt" else {}
    child = subprocess.Popen(
        [str(path)],
        cwd=str(path.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **kwargs,
    )

    _launched[:] = [c for c in _launched if c.poll() is None]
    _launched.append(child)


def looks_binary(data):
    """NUL 이 있으면 바이너리로 본다. 뷰어가 텍스트/16진수 모드를 고르는 기준."""
    return b"\x00" in data


def remove_tree(path):
    """파일이든 디렉터리든 지운다. 심링크는 따라가지 않고 링크만 지운다."""
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
        return

    # shutil.rmtree 를 쓰지 않는다. 심링크를 따라 밖을 지우지 않도록
    # 직접 훑으며 링크는 링크만 지운다
    for child in path.iterdir():
        remove_tree(child)
    path.rmdir()
