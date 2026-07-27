# 변경된 줄만 다시 그리는 더블 버퍼 렌더러와 동아시아 문자 폭 계산

import re
import unicodedata

from . import term

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def char_width(ch):
    """한 글자가 터미널에서 차지하는 칸 수.

    한글과 한자, 일본어(W/F)는 두 칸을 먹는다. 이걸 무시하면 파일 이름에 한글이
    섞인 순간 오른쪽 패널의 테두리가 어긋난다.

    A(Ambiguous)도 두 칸으로 센다. MIDDLE DOT, HORIZONTAL ELLIPSIS,
    화살표, 박스 문자가 여기 속하는데,
    한글과 CJK 환경의 터미널은 이들을 두 칸으로 그린다. 우리가 한 칸으로 세면
    줄이 계산보다 길어져 오른쪽 테두리가 밀리거나 줄이 넘쳐 화면이 깨진다.
    반대로 두 칸으로 세고 터미널이 한 칸으로 그리면 빈틈만 생긴다.
    **넘치는 쪽이 모자란 쪽보다 훨씬 나쁘므로 안전한 방향을 고른다.**

    우리가 그리는 장식 문자에는 A 를 쓰지 않는다(tests/test_width_safety.py 가
    이를 지킨다). 그래서 이 판단이 실제로 걸리는 곳은 파일 이름뿐이다.
    """
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1


def width(text):
    """ANSI 시퀀스를 제외한 실제 표시 폭."""
    return sum(char_width(ch) for ch in _ANSI.sub("", text))


def fit(text, limit):
    """표시 폭이 limit 을 넘지 않게 자른다. 잘렸으면 끝에 '~' 를 붙인다."""
    if limit <= 0:
        return ""
    if width(text) <= limit:
        return text

    out, used = [], 0
    for ch in text:
        w = char_width(ch)
        if used + w > limit - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "~"


def pad(text, target):
    """오른쪽을 공백으로 채워 정확히 target 칸을 차지하게 만든다."""
    text = fit(text, target)
    return text + " " * max(target - width(text), 0)


def rjust(text, target):
    """오른쪽 정렬. 크기 표시처럼 우측에 붙는 열에 쓴다."""
    text = fit(text, target)
    return " " * max(target - width(text), 0) + text


class Renderer:
    """줄 단위로 이전 프레임과 비교해 달라진 줄만 다시 쓴다.

    매 프레임 화면 전체를 지우고 다시 그리면 깜빡인다. 커서 이동만으로 바뀐 줄을
    덮어쓰면 SSH 로 연결한 느린 터미널에서도 부드럽게 움직인다.
    """

    def __init__(self):
        self._prev = []
        self._size = (0, 0)

    def render(self, lines, size):
        """lines 를 화면에 반영한다. size 가 바뀌면 전체를 다시 그린다."""
        if size != self._size:
            self._size = size
            self._prev = []
            term.write(term.RESET + term.CLEAR)

        cols, rows = size
        out = []
        for index in range(rows):
            line = lines[index] if index < len(lines) else ""
            if index < len(self._prev) and self._prev[index] == line:
                continue
            # \x1b[K 로 줄 끝까지 지워 이전 프레임의 잔상을 없앤다
            out.append(term.move(index + 1, 1) + line + term.RESET + "\x1b[K")

        if out:
            term.write("".join(out))
            term.flush()

        self._prev = [lines[i] if i < len(lines) else "" for i in range(rows)]

    def invalidate(self):
        """다음 프레임에 전체를 다시 그리게 만든다."""
        self._prev = []
