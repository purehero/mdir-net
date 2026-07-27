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
RESET = "\x1b[0m"

# 색상. 256색이 아니라 기본 16색만 써서 어느 터미널에서나 같게 보이게 한다
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
REVERSE = "\x1b[7m"
FG_CYAN = "\x1b[36m"
FG_YELLOW = "\x1b[33m"
FG_GREEN = "\x1b[32m"
FG_RED = "\x1b[31m"
FG_GRAY = "\x1b[90m"

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
        write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR + HOME)
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

        write(SHOW_CURSOR + RESET + ALT_SCREEN_OFF)
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
