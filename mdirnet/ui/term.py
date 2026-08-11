# raw 모드 진입/복원과 키 입력 디코딩을 OS 별로 감싸는 모듈

import atexit
import io
import os
import shutil
import signal
import sys
import time

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import msvcrt
else:
    import select
    import termios
    import tty

# 화면 제어 시퀀스
CLEAR = "\x1b[2J"
HOME = "\x1b[H"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"

# 색상. 256색이 아니라 기본 16색만 써서 어느 터미널에서나 같게 보이게 한다.
# 사용자 터미널이 흰 바탕일 수도 있으니 물려받지 않고 바닥을 직접 칠한다
HARD_RESET = "\x1b[0m"  # 터미널을 원래 색으로. 화면을 떠날 때만 쓴다
BOLD = "\x1b[1m"  # 어두운 바닥에서는 밝은 흰색이 된다 - 제목

# 글자색 번호. MDir 은 같은 색의 어두운 것과 밝은 것을 둘 다 썼다
# (문서는 청록, COM 은 하늘색=밝은청록). 그래서 색을 숫자로 들고 ink() 가
# 밝기를 붙인다. 배경은 색상셋이 정한다
BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = 30, 31, 32, 33, 34, 35, 36, 37

# 색상셋. MDir 3.06 배포판에 든 것이 이 둘뿐이다.
#   BLACK.COL - 검은 바닥. 배포판 초기치
#   BLUE.COL  - 파란 바닥. 같이 들어있던 예제
# 두 파일의 글자색은 완전히 같고 배경만 다르다. 다른 곳은 딱 한 군데인데,
# 파란 바닥에서 파랑 글자(H OBJ LIB)가 안 보이므로 검정으로 바꿔놓았다.
# 그 조정을 ink() 가 규칙으로 한다
THEMES = ("black", "blue")
_bg = BLACK  # 배경을 '같은 색의 글자색 번호' 로 들고 있는다. +10 하면 배경 코드다


def ink(color, bright=True):
    """지금 색상셋의 바닥 위에 얹는 글자색.

    글자색이 배경색과 같으면 안 보이므로 바꿔치운다. MSET.DOC 도 MDir 이
    "사용자가 지정한 색깔에 문제가 있을경우는 최대한 자동으로 조정" 한다고
    적어두었고, BLUE.COL 의 H OBJ LIB 가 실제로 그렇게 조정된 값이다.
    """
    color, bright = _visible(color, bright)
    return "\x1b[%s%d;%dm" % ("1;" if bright else "", color, _bg + 10)


def bar(color, bright=True):
    """커서 줄. 그 항목의 글자색을 바탕으로 깔고 글자를 뒤집는다.

    색을 하나로 고정하면 커서가 놓인 순간 그 파일이 무슨 종류였는지 알 수
    없게 된다. 그래서 바탕색이 항목마다 달라진다.

    밝기는 배경에 실을 수 없다. VGA 텍스트 모드의 속성 바이트에서 그 자리는
    깜빡임이었고 배경은 8색뿐이었다. MDir 도 같은 제약 아래 있었다. 그래서
    어두운 갈래는 바탕이 어두워지고, 그 위의 글자는 검정이면 안 읽힌다.
    어두운 색에는 흰 글자를 얹는다.

    밝은회색만 예외다. 밝기 표시가 없어 '어두운' 쪽으로 분류되지만 바탕은
    밝게 깔리므로 검은 글자가 맞다. BLACK.COL 의 `70`(검정 on 밝은회색)이
    바로 이 값이다.
    """
    color, bright = _visible(color, bright)
    text = BLACK if bright or color == WHITE else WHITE
    return "\x1b[%d;%dm" % (text, color + 10)


def _visible(color, bright):
    """배경에 묻히는 글자색을 보이는 색으로 바꿔준다.

    밝은 것은 묻히지 않는다. DOS 에서 밝은파랑(9)과 파랑(1)은 다른 색이고,
    배경은 어두운 8색뿐이므로 파란 바닥 위의 밝은파랑은 잘 보인다. BLUE.COL 도
    소스(밝은파랑)는 그대로 두고 H OBJ LIB(파랑)만 검정으로 바꿔놓았다.
    """
    if bright or color != _bg:
        return color, bright
    return (WHITE if _bg == BLACK else BLACK), False


def use_theme(name):
    """색상셋을 갈아끼운다. 이름은 THEMES 중 하나다."""
    global _bg, NORMAL, RESET, DIM, BAR, MARK, GOOD, BAD
    if name not in THEMES:
        raise ValueError("모르는 색상셋입니다: %s" % name)

    _bg = BLACK if name == "black" else BLUE

    NORMAL = ink(WHITE, False)  # 바탕이자 '나머지 파일' 색. BLACK.COL 의 07
    # 그리는 동안의 '보통 상태'. app.py 곳곳의 RESET 이 바탕색을 지우지 않고
    # 되돌아오게 하려면 이것이 바탕을 함께 들고 있어야 한다. 이 한 줄 덕분에
    # 줄 끝의 \x1b[K 도, 화면 지우기도, splice() 도 바닥을 유지한다
    RESET = HARD_RESET + NORMAL
    DIM = ink(CYAN, False)  # 테두리, 가로줄, 안내 문구. '메인화면 선' 색이다
    BAR = bar(WHITE, False)  # 패널 제목, 기능키 라벨, 상자 선택
    MARK = ink(YELLOW, True)  # 고른 항목, 경고
    GOOD = ink(GREEN, True)  # 연결됨, 진행바
    BAD = ink(RED, True)  # 오류


def theme():
    """지금 쓰는 색상셋 이름."""
    return "black" if _bg == BLACK else "blue"


use_theme("black")  # MDir 배포판 초기치


# 키 이름 상수
UP, DOWN, LEFT, RIGHT = "UP", "DOWN", "LEFT", "RIGHT"
ENTER, ESC, TAB, SPACE, BACKSPACE = "ENTER", "ESC", "TAB", "SPACE", "BACKSPACE"
HOME_KEY, END_KEY, PGUP, PGDN = "HOME", "END", "PGUP", "PGDN"
INSERT, DELETE = "INSERT", "DELETE"

# POSIX 터미널이 보내는 이스케이프 시퀀스 > 키 이름
_CSI_KEYS = {
    "A": UP,
    "B": DOWN,
    "C": RIGHT,
    "D": LEFT,
    "H": HOME_KEY,
    "F": END_KEY,
    "1~": HOME_KEY,
    "2~": INSERT,
    "3~": DELETE,
    "4~": END_KEY,
    "5~": PGUP,
    "6~": PGDN,
    "11~": "F1",
    "12~": "F2",
    "13~": "F3",
    "14~": "F4",
    "15~": "F5",
    "17~": "F6",
    "18~": "F7",
    "19~": "F8",
    "20~": "F9",
    "21~": "F10",
}
# ESC O 로 시작하는 형태 (일부 터미널의 F1~F4, 방향키)
_SS3_KEYS = {"P": "F1", "Q": "F2", "R": "F3", "S": "F4", "A": UP, "B": DOWN, "C": RIGHT, "D": LEFT}

# Windows 확장 키 스캔코드 > 키 이름
_WIN_EXT = {
    "H": UP,
    "P": DOWN,
    "K": LEFT,
    "M": RIGHT,
    "G": HOME_KEY,
    "O": END_KEY,
    "I": PGUP,
    "Q": PGDN,
    "R": INSERT,
    "S": DELETE,
    ";": "F1",
    "<": "F2",
    "=": "F3",
    ">": "F4",
    "?": "F5",
    "@": "F6",
    "A": "F7",
    "B": "F8",
    "C": "F9",
    "D": "F10",
}


def size():
    """터미널 크기. 리사이즈될 수 있으니 매 프레임 다시 물어본다."""
    cols, rows = shutil.get_terminal_size(fallback=(80, 24))
    return max(cols, 40), max(rows, 10)


def write(text):
    sys.stdout.write(text)


def flush():
    sys.stdout.flush()


def move(row, col):
    """1-기준 좌표로 커서를 옮기는 시퀀스."""
    return "\x1b[%d;%dH" % (row, col)


def _enable_windows_ansi():
    """Windows 10+ 콘솔에서 ANSI 해석을 켠다.

    이걸 켜지 않으면 이스케이프 시퀀스가 화면에 글자로 그대로 찍힌다.
    구형 콘솔이면 실패하는데, 그때는 그냥 포기한다 (Windows Terminal 권장).
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))


class Terminal:
    """대체 화면 버퍼로 들어가 raw 모드로 키를 읽고, 끝나면 원상복구한다.

    복원을 try/finally 하나에만 맡기지 않는다. 예외로 죽거나 시그널을 받아도
    터미널이 망가진 채 셸로 돌아가지 않도록 atexit 과 시그널 핸들러를 함께 건다.
    """

    def __init__(self):
        self._saved = None
        self._active = False
        self._prev_handlers = {}
        self._real_stderr = None
        self._stderr_buffer = None

    def __enter__(self):
        if IS_WINDOWS:
            _enable_windows_ansi()
        else:
            self._saved = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())

        # 데몬 스레드가 예외로 죽으면 트레이스백이 stderr 로 쏟아져 화면을 뭉갠다.
        # 화면을 쓰는 동안은 받아두고, 복원한 뒤에 한꺼번에 내보낸다
        self._real_stderr = sys.stderr
        self._stderr_buffer = io.StringIO()
        sys.stderr = self._stderr_buffer

        self._active = True
        # RESET 을 CLEAR 앞에 놓아야 첫 프레임 전에 터미널 색이 비치지 않는다
        write(ALT_SCREEN_ON + HIDE_CURSOR + RESET + CLEAR + HOME)
        flush()

        atexit.register(self.restore)
        for sig in (signal.SIGTERM, signal.SIGHUP) if not IS_WINDOWS else (signal.SIGTERM,):
            try:
                self._prev_handlers[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass  # 메인 스레드가 아니거나 지원하지 않는 시그널
        return self

    def __exit__(self, *exc):
        self.restore()
        return False

    def _on_signal(self, signum, frame):
        self.restore()
        raise KeyboardInterrupt

    def restore(self):
        """두 번 이상 불려도 안전해야 한다. atexit 과 __exit__ 둘 다 부른다."""
        if not self._active:
            return
        self._active = False

        # 여기서 RESET 을 쓰면 셸이 파란 바닥으로 남는다. 나갈 때는 HARD_RESET 이다
        write(SHOW_CURSOR + HARD_RESET + ALT_SCREEN_OFF)
        flush()
        if not IS_WINDOWS and self._saved is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved)

        for sig, handler in self._prev_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

        # 화면을 쓰는 동안 모아둔 stderr 를 이제 원래 자리로 흘려보낸다
        if self._real_stderr is not None:
            sys.stderr = self._real_stderr
            held = self._stderr_buffer.getvalue()
            if held:
                self._real_stderr.write(held)
            self._real_stderr = None

    # --- 키 입력 ---

    def read_key(self, timeout):
        """키 하나를 이름 문자열로 돌려준다. timeout 안에 없으면 None.

        UI 루프가 네트워크 이벤트도 처리해야 하므로 절대 무한정 기다리지 않는다.
        """
        if IS_WINDOWS:
            return self._read_key_windows(timeout)
        return self._read_key_posix(timeout)

    def _read_key_windows(self, timeout):
        # Windows 에는 select 로 stdin 을 기다릴 방법이 없어 kbhit 을 폴링한다.
        # sleep 없이 돌면 CPU 를 그대로 태운다
        for _ in range(max(int(timeout / 0.01), 1)):
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):  # 확장 키. 스캔코드가 하나 더 온다
                    return _WIN_EXT.get(msvcrt.getwch())
                return _translate_char(ch)
            time.sleep(0.01)
        return None

    def _read_key_posix(self, timeout):
        fd = sys.stdin.fileno()
        if not select.select([fd], [], [], timeout)[0]:
            return None

        first = os.read(fd, 1)
        if not first:
            return None
        if first != b"\x1b":
            # 한글처럼 여러 바이트인 문자는 남은 바이트를 이어 읽어야 한다
            return _translate_char(self._decode_utf8(fd, first))

        # ESC 로 시작하면 시퀀스일 수 있다. 아주 짧게만 뒤를 살펴본다.
        # 진짜 ESC 키를 눌렀을 때 여기서 오래 기다리면 반응이 느려진다
        if not select.select([fd], [], [], 0.03)[0]:
            return ESC

        second = os.read(fd, 1).decode("latin-1")
        if second == "O":
            return _SS3_KEYS.get(os.read(fd, 1).decode("latin-1"), None)
        if second != "[":
            return ESC

        # CSI: 숫자와 ';' 를 모으고 마지막 글자가 종료 문자다
        body = ""
        while True:
            if not select.select([fd], [], [], 0.03)[0]:
                return ESC
            ch = os.read(fd, 1).decode("latin-1")
            if ch.isdigit() or ch == ";":
                body += ch
                continue
            if ch == "~":
                return _CSI_KEYS.get(body + "~")
            return _CSI_KEYS.get(ch)

    def _decode_utf8(self, fd, first):
        """선행 바이트를 보고 필요한 만큼 더 읽어 한 글자로 만든다."""
        lead = first[0]
        if lead < 0x80:
            return chr(lead)

        need = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
        buf = bytearray(first)
        while len(buf) < need:
            if not select.select([fd], [], [], 0.02)[0]:
                break
            buf.extend(os.read(fd, 1))
        return buf.decode("utf-8", "replace")


# 두벌식 자판에서 한글 입력이 켜져 있으면 y 를 눌러도 터미널에는 'ㅛ' 가 들어온다.
# 그래서 y/n 같은 단축키가 먹지 않는다. 명령으로 해석할 때만 원래 글자로 되돌린다.
# 입력창에서는 절대 되돌리면 안 된다. 한글 파일 이름을 적을 수 없게 된다.
_HANGUL_TO_QWERTY = {
    "ㅂ": "q", "ㅈ": "w", "ㄷ": "e", "ㄱ": "r", "ㅅ": "t",
    "ㅛ": "y", "ㅕ": "u", "ㅑ": "i", "ㅐ": "o", "ㅔ": "p",
    "ㅁ": "a", "ㄴ": "s", "ㅇ": "d", "ㄹ": "f", "ㅎ": "g",
    "ㅗ": "h", "ㅓ": "j", "ㅏ": "k", "ㅣ": "l",
    "ㅋ": "z", "ㅌ": "x", "ㅊ": "c", "ㅍ": "v", "ㅠ": "b",
    "ㅜ": "n", "ㅡ": "m",
    # 윗글쇠
    "ㅃ": "Q", "ㅉ": "W", "ㄸ": "E", "ㄲ": "R", "ㅆ": "T", "ㅒ": "O", "ㅖ": "P",
}


def command_key(key):
    """한글 입력 상태로 눌린 글자를 원래 영문 단축키로 되돌린다.

    매핑에 없으면 그대로 반환한다. 방향키나 기능키 같은 이름 상수도 그대로 지난다.
    """
    return _HANGUL_TO_QWERTY.get(key, key)


def _translate_char(ch):
    """평범한 문자를 키 이름으로 정규화한다."""
    if ch in ("\r", "\n"):
        return ENTER
    if ch == "\t":
        return TAB
    if ch == " ":
        return SPACE
    if ch in ("\x7f", "\x08"):
        return BACKSPACE
    if ch == "\x03":
        return "CTRL-C"
    if ch == "\x04":
        return "CTRL-D"
    if ch == "\x1b":
        return ESC
    return ch
