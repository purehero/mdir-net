# 듀얼 패널 상태 기계, 키 바인딩, 다이얼로그를 담은 UI 본체

import fnmatch
import os
import time
import queue
import threading
from pathlib import Path

from .. import config, discovery, fsops, session, shares as shares_mod, winfw
from . import screen, term

# 테두리 문자. 폭이 정확히 1칸이고 East Asian Width 가 A(Ambiguous)가 아닌 것만
# 쓴다. 박스 문자(U+2500~257F) 112자는 전부 Ambiguous 라 한글 터미널에서 두 칸으로
# 그려져 화면을 밀어버린다. 모서리 전용 문자(U+231C~231F)는 EAW 가 N 이라 안전하다
BOX_H, BOX_V = "-", "|"
BOX_TL, BOX_TR, BOX_BL, BOX_BR = "\u231c", "\u231d", "\u231e", "\u231f"

# \ud328\ub110 \uc704\uc544\ub798 \uac00\ub85c\uc904. `=` \ub294 \uadf8 \uc790\uccb4\ub85c \ub450 \uc904\ucc98\ub7fc \ubcf4\uc774\ubbc0\ub85c `\u2550`(\ud3ed\uc774 \uc560\ub9e4\ud574 \ubabb \uc4f4\ub2e4)
# \ub300\uc2e0 \uc4f4\ub2e4. \ud55c \uc904\uc774\uba74 \ucda9\ubd84\ud574 \ubcf8\ubb38\uc774 \uadf8\ub9cc\ud07c \ub113\uc5b4\uc9c4\ub2e4
PANE_RULE = "="

# MDir 처럼 이름, 확장자, 크기, 날짜, 시간을 칸으로 나눠 보여준다.
#
# 확장자/크기/날짜/시간은 **폭이 고정**이고, 남는 자리를 이름이 모두 가져간다.
# 그래야 창을 넓히면 긴 파일 이름이 그만큼 더 보이고, 오른쪽 칸들은 늘 같은
# 자리에 있어 눈이 따라가기 쉽다.
EXT_COL = 4    # HTML, JSON, JPEG, XLSX 같은 네 글자 확장자가 흔하다
SIZE_COL = 10  # `[ SubDir ]` 이 10칸, 사람이 읽는 크기는 최대 `1023.0 GB` 9칸
DATE_COL = 8   # 26-07-28
TIME_COL = 6   # 11:14a. 두 자리 시각이면 6칸이다. 5로 잡으면 `11:1~` 로 잘린다
NAME_MIN = 6

# 고정 칸이 차지하는 총 폭 (표시 2 + 확장자 5 + 크기 10 + 날짜 9 + 시간 7)
FIXED_COLS = 2 + (EXT_COL + 1) + SIZE_COL + (DATE_COL + 1) + (TIME_COL + 1)


class Transfer:
    """진행 중인 복사 작업. 워커가 갱신하고 UI 가 매 프레임 읽어간다.

    청크마다 이벤트를 큐에 넣으면 큐가 넘치므로, 공용 객체를 두고 UI 가
    폴링하는 쪽을 택했다. 정수와 문자열 대입은 GIL 아래에서 찢어지지 않는다.
    """

    def __init__(self, label, total):
        self.label = label
        self.total = total
        self.done = 0
        self.current = ""
        self.cancelled = False
        self.started = time.monotonic()

    @property
    def ratio(self):
        return min(self.done / self.total, 1.0) if self.total else 0.0

    @property
    def speed(self):
        """초당 바이트. 시작 직후에는 표본이 없어 0 이다."""
        elapsed = time.monotonic() - self.started
        return self.done / elapsed if elapsed > 0.5 and self.done else 0.0

    def rate_text(self):
        """`12.4 MB/s . 약 2분 남음` 형태. 남은 시간은 지금 속도 기준의 추정이다."""
        speed = self.speed
        if not speed:
            return "속도 재는 중..."

        text = "%s/s" % fsops.human_size(int(speed))
        left = self.total - self.done
        if left <= 0:
            return text

        seconds = int(left / speed)
        if seconds < 60:
            return "%s . 약 %d초 남음" % (text, max(seconds, 1))
        if seconds < 3600:
            return "%s . 약 %d분 남음" % (text, seconds // 60)
        return "%s . 약 %d시간 %d분 남음" % (text, seconds // 3600, (seconds % 3600) // 60)


class Pane:
    """한쪽 패널. 로컬이든 원격이든 같은 방식으로 다룬다."""

    def __init__(self, app, is_remote):
        self.app = app
        self.is_remote = is_remote
        # 로컬은 내 실제 경로(Path), 원격은 공유 상대 경로 문자열이다.
        # 원격에 호스트 절대 경로를 쓰지 않으므로 OS 경로 규칙이 섞이지 않는다
        self.path = shares_mod.ROOT if is_remote else Path.home()
        self.entries = []
        self.cursor = 0
        self.offset = 0
        self.selected = set()
        self.message = ""
        self.sort_mode = fsops.SORT_NAME
        self._generation = 0  # 늦게 도착한 조회 결과를 버리기 위한 세대 번호

    @property
    def label(self):
        """패널 이름. 왼쪽은 늘 내 PC, 오른쪽은 늘 상대다."""
        if not self.is_remote:
            return "내 PC"
        return self.app.session.peer_name if self.app.session else "상대"

    @property
    def location(self):
        """지금 보고 있는 위치."""
        if self.is_remote and not self.app.session:
            return "(연결되지 않음)"
        return shares_mod.display(self.path) if self.is_remote else str(self.path)

    @property
    def title(self):
        return "%s  %s" % (self.label, self.location)

    @property
    def active(self):
        return not self.is_remote or self.app.session is not None

    @property
    def at_top(self):
        """더 위로 올라갈 수 없는 지점인가."""
        if self.is_remote:
            return shares_mod.normalize(self.path) == shares_mod.ROOT
        return self.path == self.path.parent

    def child(self, name):
        """이 패널의 경로 공간에서 하위 항목의 경로를 만든다."""
        if self.is_remote:
            return shares_mod.join(self.path, name)
        return self.path / name

    def up(self):
        return shares_mod.parent(self.path) if self.is_remote else self.path.parent

    def rows(self):
        """'..' 항목을 앞에 붙인 표시용 목록."""
        if not self.active:
            return []
        items = []
        if not self.at_top:
            items.append({"name": "..", "is_dir": True, "size": 0, "up": True})
        return items + self.entries

    def current_item(self):
        rows = self.rows()
        return rows[self.cursor] if 0 <= self.cursor < len(rows) else None

    def targets(self):
        """복사 대상. 선택된 게 있으면 그것들, 없으면 커서 항목 하나."""
        if self.selected:
            return [e for e in self.entries if e["name"] in self.selected]
        item = self.current_item()
        if item and not item.get("up"):
            return [item]
        return []

    def set_remote(self, remote):
        """이 패널이 볼 대상을 바꾼다. 경로 공간이 달라지므로 처음으로 되돌린다."""
        if self.is_remote == remote:
            return
        self.is_remote = remote
        self.path = shares_mod.ROOT if remote else Path.home()
        self.cursor = 0
        self.offset = 0
        self.selected.clear()
        self.refresh()

    def navigate(self, path):
        self.path = path if self.is_remote else Path(path)
        self.cursor = 0
        self.offset = 0
        self.selected.clear()
        self.refresh()

    def refresh(self):
        """목록을 다시 읽는다. 원격이면 워커 스레드로 넘겨 UI 를 막지 않는다."""
        if not self.active:
            self.entries = []
            return

        self._generation += 1
        generation = self._generation
        side = "remote" if self.is_remote else "local"

        if not self.is_remote:
            try:
                self.entries = fsops.sort_entries(
                    self._visible(fsops.list_dir(self.path)), self.sort_mode
                )
                self.message = ""
            except OSError as exc:
                self.entries = []
                self.message = "읽을 수 없습니다: %s" % exc
            self._clamp()
            return

        self.message = "불러오는 중..."
        active = self.app.session

        def work():
            try:
                path, entries = active.remote_list(self.path)
                self.app.events.put(("listing", side, generation, path, entries))
            except session.SessionError as exc:
                self.app.events.put(("list_error", side, generation, str(exc)))
            except Exception as exc:  # noqa: BLE001
                # 예상 못한 예외를 삼키면 패널이 '불러오는 중...' 에서 멈춘 채
                # 이유를 알 수 없게 된다. 반드시 화면에 남긴다
                self.app.events.put(
                    ("list_error", side, generation, "%s: %s" % (type(exc).__name__, exc))
                )

        threading.Thread(target=work, name="list-%s" % side, daemon=True).start()

    def _visible(self, entries):
        """숨김 파일(이름이 . 로 시작)을 옵션에 따라 걸러낸다."""
        if self.app.show_hidden:
            return entries
        return [e for e in entries if not e["name"].startswith(".")]

    def apply_listing(self, generation, path, entries):
        if generation != self._generation:
            return  # 그 사이 다른 디렉터리로 옮겨갔다. 낡은 결과는 버린다
        self.path = path if self.is_remote else Path(path)
        self.entries = fsops.sort_entries(self._visible(entries), self.sort_mode)
        self.message = ""
        self._clamp()

    def apply_list_error(self, generation, msg):
        if generation != self._generation:
            return
        self.entries = []
        self.message = msg
        self._clamp()

    def _clamp(self):
        count = len(self.rows())
        self.cursor = max(0, min(self.cursor, count - 1)) if count else 0

    def move(self, delta, page=1):
        rows = self.rows()
        if not rows:
            return
        self.cursor = max(0, min(self.cursor + delta * page, len(rows) - 1))

    def cycle_sort(self):
        """MDir 처럼 키 하나로 정렬 기준을 돌린다."""
        order = fsops.SORT_ORDER
        self.sort_mode = order[(order.index(self.sort_mode) + 1) % len(order)]
        self.entries = fsops.sort_entries(self.entries, self.sort_mode)
        self._clamp()
        return fsops.SORT_LABELS[self.sort_mode]

    def select_mask(self, pattern, add=True):
        """`*.txt` 같은 패턴으로 한꺼번에 고르거나 푼다.

        대소문자를 가리지 않는다. `*.TXT` 로도 .txt 가 잡히는 편이 자연스럽다.
        """
        lowered = pattern.lower()
        hit = [e["name"] for e in self.entries if fnmatch.fnmatch(e["name"].lower(), lowered)]
        if add:
            self.selected.update(hit)
        else:
            self.selected.difference_update(hit)
        return len(hit)

    def toggle_select(self):
        item = self.current_item()
        if not item or item.get("up"):
            return
        self.selected.symmetric_difference_update({item["name"]})
        self.move(1)

    def scroll_into_view(self, height):
        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + height:
            self.offset = self.cursor - height + 1
        self.offset = max(0, min(self.offset, max(len(self.rows()) - height, 0)))


class Dialog:
    """모달 다이얼로그의 공통 껍데기. lines() 를 그리고 key() 로 입력을 받는다."""

    title = ""
    max_body = None  # 그릴 수 있는 최대 줄 수. 화면을 그리기 직전에 앱이 채워준다

    def lines(self, inner_width):
        return []

    def key(self, key):
        """True 를 돌려주면 다이얼로그를 닫는다."""
        return False


class InputDialog(Dialog):
    """한 줄 입력을 받는다. 선택 마스크와 폴더 만들기가 함께 쓴다."""

    def __init__(self, title, prompt, on_submit, initial=""):
        self.title = title
        self.text = initial
        # 커서 위치. 이름 바꾸기는 기존 이름이 채워진 채로 열리므로, 끝에 붙이기만
        # 되면 앞부분을 고치려고 전부 지웠다 다시 쳐야 한다
        self.cursor = len(initial)
        self._prompt = prompt if isinstance(prompt, list) else [prompt]
        self._on_submit = on_submit

    def _rendered(self, inner):
        """커서 자리의 글자를 반전시켜 보여준다. 끝이면 빈칸을 반전시킨다."""
        head = self.text[: self.cursor]
        at = self.text[self.cursor : self.cursor + 1] or " "
        tail = self.text[self.cursor + 1 :]
        body = term.BOLD + head + term.RESET + term.REVERSE + at + term.RESET
        body += term.BOLD + tail + term.RESET
        return screen.pad(body, inner)

    def lines(self, inner):
        return (
            self._prompt
            + ["", self._rendered(inner), ""]
            + [term.FG_GRAY + "Enter 확인   Esc 취소   < > Home End 이동" + term.RESET]
        )

    def key(self, key):
        # q 도 입력해야 하므로 취소는 Esc 뿐이다
        if key == term.ENTER:
            if self.text:
                self._on_submit(self.text)
            return True
        if key == term.ESC:
            return True

        if key == term.LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key == term.RIGHT:
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key == term.HOME_KEY:
            self.cursor = 0
        elif key == term.END_KEY:
            self.cursor = len(self.text)
        elif key == term.BACKSPACE:
            if self.cursor:
                self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
                self.cursor -= 1
        elif key == term.DELETE:
            self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]
        elif key == term.SPACE:
            self._insert(" ")
        elif len(key) == 1 and key.isprintable():
            self._insert(key)
        return False

    def _insert(self, text):
        self.text = self.text[: self.cursor] + text + self.text[self.cursor :]
        self.cursor += len(text)


class ViewerDialog(Dialog):
    """MDir 의 파일 보기. 텍스트는 그대로, 바이너리는 16진수로 보여준다."""

    BYTES_PER_ROW = 16

    def __init__(self, name, data, total_size):
        self.title = "보기: %s" % name
        self.offset = 0
        self.total_size = total_size
        self.shown_bytes = len(data)
        self.binary = fsops.looks_binary(data)
        self.body = (
            self._hex_rows(data)
            if self.binary
            else (data.decode("utf-8", "replace").splitlines() or [""])
        )

    def _hex_rows(self, data):
        rows = []
        for start in range(0, len(data), self.BYTES_PER_ROW):
            chunk = data[start : start + self.BYTES_PER_ROW]
            hexed = " ".join("%02x" % b for b in chunk)
            # 출력 가능한 문자만 그대로, 나머지는 점으로
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            rows.append("%08x  %-47s  %s" % (start, hexed, ascii_part))
        return rows

    def _height(self):
        return max((self.max_body or 18) - 4, 5)

    def lines(self, inner):
        height = self._height()
        self.offset = max(0, min(self.offset, max(len(self.body) - height, 0)))

        out = []
        for index in range(self.offset, min(self.offset + height, len(self.body))):
            number = term.FG_GRAY + "%5d " % (index + 1) + term.RESET
            out.append(number + screen.fit(self.body[index].rstrip("\r"), inner - 6))
        while len(out) < height:
            out.append("")

        note = "%s . %s" % (
            "16진수" if self.binary else "텍스트",
            fsops.human_size(self.total_size),
        )
        if self.shown_bytes < self.total_size:
            note += " (앞 %s 만)" % fsops.human_size(self.shown_bytes)

        out += [
            term.FG_GRAY + BOX_H * inner + term.RESET,
            term.FG_GRAY
            + screen.pad("%s   %d/%d 줄   ^v PgUp/PgDn 스크롤   q 닫기" % (
                note, min(self.offset + height, len(self.body)), len(self.body)), inner)
            + term.RESET,
        ]
        return out

    def key(self, key):
        height = self._height()
        if key in (term.UP, "k"):
            self.offset -= 1
        elif key in (term.DOWN, "j"):
            self.offset += 1
        elif key == term.PGUP:
            self.offset -= height
        elif key == term.PGDN:
            self.offset += height
        elif key == term.HOME_KEY:
            self.offset = 0
        elif key == term.END_KEY:
            self.offset = len(self.body)
        elif key in (term.ESC, "q", term.ENTER, "F3", "v"):
            return True
        return False


class MessageDialog(Dialog):
    """오류와 안내 표시. 아무 키나 누르면 닫힌다.

    내용이 화면보다 길면 잘리는데, 잘렸다는 표시도 스크롤도 없으면 사용자는
    거기까지가 전부인 줄 안다. 길면 스크롤할 수 있게 한다.
    """

    def __init__(self, title, body, color=""):
        self.title = title
        self._body = body if isinstance(body, list) else [body]
        self._color = color
        self.offset = 0

    def _room(self):
        # 제목, 구분선, 안내줄을 뺀 본문 자리
        return max((self.max_body or 18) - 4, 3)

    @property
    def scrollable(self):
        return len(self._body) > self._room()

    def lines(self, inner_width):
        room = self._room()
        if not self.scrollable:
            return [self._color + line + term.RESET for line in self._body] + [
                "",
                term.FG_GRAY + "아무 키나 누르면 닫힙니다" + term.RESET,
            ]

        self.offset = max(0, min(self.offset, len(self._body) - room))
        window = self._body[self.offset : self.offset + room]
        return [self._color + line + term.RESET for line in window] + [
            "",
            term.FG_GRAY
            + "%d-%d / %d줄   위아래로 스크롤   q 닫기"
            % (self.offset + 1, self.offset + len(window), len(self._body))
            + term.RESET,
        ]

    def key(self, key):
        if not self.scrollable:
            return True

        room = self._room()
        if key in (term.UP, "k"):
            self.offset -= 1
        elif key in (term.DOWN, "j"):
            self.offset += 1
        elif key == term.PGUP:
            self.offset -= room
        elif key == term.PGDN:
            self.offset += room
        elif key == term.HOME_KEY:
            self.offset = 0
        elif key == term.END_KEY:
            self.offset = len(self._body)
        else:
            return True  # 그 외의 키는 닫는다
        return False


class ConfirmDialog(Dialog):
    """예/아니오 확인. 결정되면 on_decide(bool) 을 부른다.

    승인 다이얼로그와 같은 이유로 방향키 선택도 함께 받는다.
    되돌릴 수 없는 작업(삭제)이 있으므로 커서는 취소에서 시작한다.
    """

    def __init__(self, title, body, on_decide):
        self.title = title
        self._body = body if isinstance(body, list) else [body]
        self._on_decide = on_decide
        self.proceed = False

    def lines(self, inner_width):
        def button(text, chosen):
            style = term.REVERSE if chosen else term.FG_GRAY
            return style + "  %s  " % text + term.RESET

        return self._body + [
            "",
            button("진행(Y)", self.proceed) + "   " + button("취소(N)", not self.proceed),
            "",
            term.FG_GRAY + "< > 선택   Enter 확정   Esc 취소" + term.RESET,
        ]

    def key(self, key):
        if key in ("y", "Y"):
            self._on_decide(True)
            return True
        if key in ("n", "N", term.ESC):
            self._on_decide(False)
            return True

        if key in (term.LEFT, term.RIGHT, term.TAB, term.SPACE, "h", "l"):
            self.proceed = not self.proceed
            return False
        if key == term.ENTER:
            self._on_decide(self.proceed)
            return True
        return False


class ApprovalDialog(Dialog):
    """상대가 보낸 연결 요청. 사용자가 직접 수락/거부를 고른다.

    글자 단축키에만 의존하면 안 된다. 한글 입력이 켜져 있으면 y/n 이 'ㅛ'/'ㅜ' 로
    들어와 아무 반응이 없어서, 사용자는 다이얼로그가 먹통이라고 느낀다.
    그래서 방향키와 Tab, Space 로 고르고 Enter 로 확정하는 길을 함께 둔다.
    """

    title = "연결 요청"

    def __init__(self, request, deadline=None):
        self.request = request
        self.accept = True  # 커서가 놓인 버튼. 흔한 경우가 수락이므로 여기서 시작한다
        self.deadline = deadline

    def lines(self, inner_width):
        def button(text, chosen):
            style = term.REVERSE if chosen else term.FG_GRAY
            return style + "  %s  " % text + term.RESET

        return [
            term.BOLD + self.request.name + term.RESET,
            term.FG_GRAY + "%s . %s" % (self.request.host, self.request.platform) + term.RESET,
            "",
            "이 PC 의 공유 폴더를 열어줍니다.",
            "",
            button("수락(Y)", self.accept) + "   " + button("거부(N)", not self.accept),
            "",
            term.FG_GRAY + self._deadline_text() + term.RESET,
            term.FG_GRAY + "< > 선택   Enter 확정   Esc 거부" + term.RESET,
        ]

    def _deadline_text(self):
        """남은 시간을 매 프레임 갱신한다. 멈춘 화면처럼 보이지 않게."""
        if self.deadline is None:
            return "결정하지 않으면 거부됩니다."
        left = max(int(self.deadline - time.monotonic()), 0)
        return "%d초 안에 결정하지 않으면 거부됩니다." % left

    def key(self, key):
        if key in ("y", "Y"):
            self.request.decide(True)
            return True
        if key in ("n", "N", term.ESC):
            self.request.decide(False)
            return True

        # 글자 키 없이도 고를 수 있어야 한다
        if key in (term.LEFT, term.RIGHT, term.TAB, term.SPACE, "h", "l"):
            self.accept = not self.accept
            return False
        if key == term.ENTER:
            self.request.decide(self.accept)
            return True
        return False


class MenuBar(Dialog):
    """Norton Commander 식 상단 풀다운 메뉴.

    `Left` 와 `Right` 가 각 패널을 설정하고, 가운데 메뉴들이 기능을 모은다.
    F9 로 열고 화살표로 옮긴다. 일반 다이얼로그와 그리는 방식이 달라
    render_into() 로 직접 그린다 (제목 줄과 그 아래 상자를 함께 놓는다).
    """

    SEPARATOR = "-"

    def __init__(self, app):
        self.app = app
        self.menu = 1  # 처음에는 Files 를 편다 (NC 도 그랬다)
        self.item = 0
        self.menus = self._build()

    def _build(self):
        """(제목, [(라벨, 키힌트, 동작)]) 목록. 동작이 None 이면 구분선."""
        app = self.app
        sep = (self.SEPARATOR, "", None)

        def pane_menu(pane):
            return [
                ("이름순 정렬", "", lambda: app.set_sort(pane, fsops.SORT_NAME)),
                ("확장자순 정렬", "", lambda: app.set_sort(pane, fsops.SORT_EXT)),
                ("크기순 정렬", "", lambda: app.set_sort(pane, fsops.SORT_SIZE)),
                ("날짜순 정렬", "", lambda: app.set_sort(pane, fsops.SORT_DATE)),
                sep,
                ("새로고침", "r", pane.refresh),
            ]

        left = pane_menu(app.local)
        left += [("드라이브 바꾸기", "\\", app._open_drive_picker)]

        right = [
            (
                ("상대 보기" if not app.right.is_remote else "내 PC 보기"),
                "=",
                app.toggle_right_source,
            ),
            sep,
        ] + pane_menu(app.remote)
        if not app.right.is_remote:
            right += [("드라이브 바꾸기", "\\", app._open_drive_picker)]

        return [
            ("Left", left),
            (
                "Files",
                [
                    ("파일 보기", "3", app._view_file),
                    ("복사", "5", app._start_copy),
                    ("이름 바꾸기", "6", app._ask_rename),
                    ("폴더 만들기", "7", app._ask_mkdir),
                    ("삭제", "8", app._ask_delete),
                    sep,
                    ("도움말", "1", app._show_help),
                    ("끝내기", "0", app._quit),
                ],
            ),
            (
                "Commands",
                [
                    ("연결할 PC 찾기", "n", app._open_peer_dialog),
                    ("연결 끊기", "d", app._disconnect),
                    sep,
                    ("공유 폴더 관리", "4", app._open_share_manager),
                ],
            ),
            (
                "Options",
                [
                    (
                        ("숨김 파일 감추기" if app.show_hidden else "숨김 파일 보기"),
                        ".",
                        app.toggle_hidden,
                    ),
                ],
            ),
            ("Right", right),
        ]

    # --- 그리기 ---

    def bar_line(self, cols):
        """상단 제목 줄. 펼쳐진 메뉴만 반전으로 표시한다."""
        parts = [" "]
        for index, (title, _) in enumerate(self.menus):
            chunk = "  %s  " % title
            parts.append(term.REVERSE + chunk + term.RESET if index == self.menu else chunk)
        return screen.pad("".join(parts), cols)

    def _bar_offset(self):
        """펼쳐진 메뉴의 왼쪽 위치. 상자를 그 아래에 붙인다."""
        offset = 1
        for index, (title, _) in enumerate(self.menus):
            if index == self.menu:
                return offset
            offset += screen.width("  %s  " % title)
        return offset

    def _box(self):
        title, items = self.menus[self.menu]
        width = max(screen.width(l) + (len(k) + 2 if k else 0) for l, k, _ in items) + 4
        width = max(width, 20)

        rows = [BOX_TL + BOX_H * width + BOX_TR]
        for index, (label, hint, action) in enumerate(items):
            if action is None:
                rows.append(BOX_V + BOX_H * width + BOX_V)
                continue
            text = " " + label + " " * max(width - screen.width(label) - len(hint) - 2, 1) + hint
            text = screen.pad(text, width)
            body = term.REVERSE + text + term.RESET if index == self.item else text
            rows.append(BOX_V + body + BOX_V)
        rows.append(BOX_BL + BOX_H * width + BOX_BR)
        return rows

    def render_into(self, lines, cols, rows):
        lines[0] = self.bar_line(cols)
        left = min(self._bar_offset(), max(cols - 24, 0))

        for index, row in enumerate(self._box()):
            target = 1 + index
            if target >= rows:
                break
            lines[target] = screen.pad(
                screen.splice(lines[target], left, term.FG_CYAN + row + term.RESET), cols
            )

    # --- 조작 ---

    def _items(self):
        return self.menus[self.menu][1]

    def _step(self, delta):
        items = self._items()
        for _ in range(len(items)):
            self.item = (self.item + delta) % len(items)
            if items[self.item][2] is not None:
                return  # 구분선은 건너뛴다

    def key(self, key):
        if key in (term.LEFT, "h"):
            self.menu = (self.menu - 1) % len(self.menus)
            self.menus = self._build()
            self.item = 0
            self._step(0) if self._items()[0][2] else self._step(1)
        elif key in (term.RIGHT, "l"):
            self.menu = (self.menu + 1) % len(self.menus)
            self.menus = self._build()
            self.item = 0
            self._step(0) if self._items()[0][2] else self._step(1)
        elif key in (term.UP, "k"):
            self._step(-1)
        elif key in (term.DOWN, "j"):
            self._step(1)
        elif key == term.ENTER:
            action = self._items()[self.item][2]
            self.app.modal = None  # 동작이 새 창을 띄울 수 있으므로 먼저 닫는다
            if action:
                action()
            return False
        elif key in (term.ESC, "q", "F9", "9", "/"):
            return True
        return False


class ShareDialog(Dialog):
    """공유 폴더 목록 관리. 추가와 삭제를 여기서 한다.

    상대에게는 이 목록이 가상 루트의 폴더들로 보인다.
    """

    title = "공유 폴더 관리"
    VISIBLE = 8

    def __init__(self, app):
        self.app = app
        self.cursor = 0

    @property
    def shares(self):
        return list(self.app.shares)

    def lines(self, inner):
        shares = self.shares
        out = [
            "상대에게는 이 목록이 최상위 폴더로 보입니다.",
            term.FG_GRAY + BOX_H * inner + term.RESET,
        ]

        if not shares:
            out.append(term.FG_YELLOW + "  아직 공유하는 폴더가 없습니다." + term.RESET)
            out += [""] * (self.VISIBLE - 1)
        else:
            self.cursor = max(0, min(self.cursor, len(shares) - 1))
            start = max(0, min(self.cursor - self.VISIBLE + 1, len(shares) - self.VISIBLE))
            start = max(start, 0)
            for index in range(start, min(start + self.VISIBLE, len(shares))):
                share = shares[index]
                # 외장 디스크를 빼면 경로가 없어진다. 지우지 않고 표시만 해준다.
                # 표시가 경로 뒤에 붙으면 잘려 사라지므로 이름 옆에 둔다
                mark = "" if share.exists else "(없음)"
                head = "  %s %s" % (screen.pad(share.name, 16), screen.pad(mark, 6))
                path_room = inner - screen.width(head)
                label = screen.pad(head + screen.fit_left(str(share.path), path_room), inner)
                if index == self.cursor:
                    out.append(term.REVERSE + label + term.RESET)
                elif not share.exists:
                    out.append(term.FG_RED + label + term.RESET)
                else:
                    out.append(label)
            out += [""] * (self.VISIBLE - min(len(shares), self.VISIBLE))

        out += [
            term.FG_GRAY + BOX_H * inner + term.RESET,
            term.BOLD + "a 추가" + term.RESET
            + term.FG_GRAY + "   d 삭제   Esc 닫기" + term.RESET,
        ]
        return out

    def key(self, key):
        shares = self.shares

        if key in (term.UP, "k"):
            self.cursor = max(0, self.cursor - 1)
        elif key in (term.DOWN, "j"):
            self.cursor = min(len(shares) - 1, self.cursor + 1) if shares else 0
        elif key in ("a", term.INSERT):
            start = shares[self.cursor].path if shares else Path.home()
            # 취소하면 이 창으로 돌아온다. 여러 폴더를 잇달아 더하고 뺄 수
            # 있어야 하므로, 선택상자를 닫았다고 관리 창까지 닫으면 안 된다
            self.app._show_share_picker(start, on_cancel=self._reopen)
            return False  # 선택상자가 이 창을 대신한다
        elif key in ("d", term.DELETE) and shares:
            self._confirm_remove(shares[self.cursor])
            return False
        elif key in (term.ESC, "q"):
            return True
        return False

    def _reopen(self):
        """관리 창을 커서 자리까지 그대로 되돌린다."""
        self.app.modal = ShareDialog(self.app)
        self.app.modal.cursor = self.cursor

    def _confirm_remove(self, share):
        def decide(ok):
            if ok:
                self.app.remove_share(share.name)
                self.cursor = max(0, self.cursor - 1)
            self._reopen()

        self.app.modal = ConfirmDialog(
            "공유 폴더 제거",
            [
                term.BOLD + share.name + term.RESET,
                term.FG_GRAY + str(share.path) + term.RESET,
                "",
                "목록에서만 빼냅니다. 폴더와 파일은 그대로 남습니다.",
            ],
            decide,
        )


class DriveDialog(Dialog):
    """드라이브(볼륨) 선택. Windows 에서 D: 로 넘어가는 유일한 통로다."""

    title = "드라이브 선택"

    def __init__(self, drives, on_choose):
        self.drives = drives
        self.cursor = 0
        self._on_choose = on_choose

    def lines(self, inner):
        if not self.drives:
            return [term.FG_GRAY + "고를 수 있는 드라이브가 없습니다." + term.RESET]

        self.cursor = max(0, min(self.cursor, len(self.drives) - 1))
        out = []
        for index, drive in enumerate(self.drives):
            kind = (" . %s" % drive["kind"]) if drive["kind"] else ""
            label = screen.pad("  %-4s %s%s" % (drive["label"], drive["path"], kind), inner)
            out.append(term.REVERSE + label + term.RESET if index == self.cursor else label)

        out += ["", term.FG_GRAY + "Enter 이동   Esc 취소" + term.RESET]
        return out

    def key(self, key):
        if key in (term.UP, "k"):
            self.cursor = max(0, self.cursor - 1)
        elif key in (term.DOWN, "j"):
            self.cursor = min(len(self.drives) - 1, self.cursor + 1) if self.drives else 0
        elif key == term.ENTER and self.drives:
            self._on_choose(self.drives[self.cursor]["path"])
            return True
        elif key in (term.ESC, "q"):
            return True
        return False


class FolderDialog(Dialog):
    """공유할 폴더를 고르는 선택상자.

    네이티브 GUI 창(tkinter)을 쓰지 않는다. tkinter 는 표준 라이브러리이긴 하지만
    GUI 세션이 있어야 하고 Linux 에서는 별도 설치가 필요한 경우가 흔해서,
    SSH 로 접속한 터미널에서는 창이 뜨지 못한다. 앱 안에서 직접 그리면
    어느 환경에서나 똑같이 동작한다.
    """

    title = "공유할 폴더 선택"
    VISIBLE = 10  # 한 번에 보여줄 폴더 수

    DRIVE_ROW = "[드라이브]"

    def __init__(self, start, on_choose, on_cancel, cancel_hint="Esc 취소", on_drives=None):
        self.path = Path(start)
        self.cursor = 0
        self.offset = 0
        self.entries = []
        self.message = ""
        self.cancel_hint = cancel_hint
        self._on_choose = on_choose
        self._on_cancel = on_cancel
        # 드라이브 루트(C:\)에서는 '..' 이 없다. 여기서 D: 로 갈 길을 열어주지
        # 않으면 다른 드라이브의 폴더를 공유할 방법이 아예 없다
        self._on_drives = on_drives
        self._load()

    def _load(self):
        """하위 폴더만 읽어들인다. 파일은 고를 수 없으니 보여주지 않는다."""
        try:
            self.entries = [e["name"] for e in fsops.list_dir(self.path) if e["is_dir"]]
            self.message = ""
        except OSError as exc:
            self.entries = []
            self.message = "읽을 수 없습니다: %s" % exc
        self.cursor = 0
        self.offset = 0

    def rows(self):
        if not fsops.is_filesystem_root(self.path):
            return [".."] + self.entries
        # 최상위에서는 '..' 대신 드라이브 목록으로 나가는 문을 둔다
        return ([self.DRIVE_ROW] if self._on_drives else []) + self.entries

    def lines(self, inner):
        rows = self.rows()

        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + self.VISIBLE:
            self.offset = self.cursor - self.VISIBLE + 1

        out = [
            term.BOLD + screen.fit_left(str(self.path), inner) + term.RESET,
            term.FG_GRAY + BOX_H * inner + term.RESET,
        ]

        if not rows:
            out.append(term.FG_GRAY + "  (하위 폴더가 없습니다)" + term.RESET)
        for index in range(self.offset, min(self.offset + self.VISIBLE, len(rows))):
            name = rows[index]
            plain = name in ("..", self.DRIVE_ROW)
            label = screen.pad("  " + (name if plain else name + "/"), inner)
            if index == self.cursor:
                out.append(term.REVERSE + label + term.RESET)
            elif name == self.DRIVE_ROW:
                out.append(term.FG_YELLOW + label + term.RESET)
            else:
                out.append(term.FG_CYAN + label + term.RESET)

        # 목록이 짧아도 상자 높이를 유지해 다이얼로그가 들썩이지 않게 한다
        for _ in range(self.VISIBLE - max(len(rows), 1)):
            out.append(" " * inner)

        # 아래 두 줄은 내용이 없어도 자리를 지킨다. 폴더를 옮길 때마다 상자
        # 높이가 달라지면 화면이 들썩여 눈에 거슬린다
        if len(rows) > self.VISIBLE:
            out.append(term.FG_GRAY + "  (%d/%d)" % (self.cursor + 1, len(rows)) + term.RESET)
        else:
            out.append(" " * inner)
        out.append(
            term.FG_RED + screen.fit(self.message, inner) + term.RESET
            if self.message
            else " " * inner
        )

        drive_hint = "   \\ 드라이브" if self._on_drives else ""
        out += [
            "",
            term.BOLD + "Ctrl-D 이 폴더로 결정" + term.RESET
            + term.FG_GRAY
            + "   Enter 열기   < 상위%s   %s" % (drive_hint, self.cancel_hint)
            + term.RESET,
        ]
        return out

    def key(self, key):
        rows = self.rows()

        if key in (term.UP, "k"):
            self.cursor = max(0, self.cursor - 1)
        elif key in (term.DOWN, "j"):
            self.cursor = min(len(rows) - 1, self.cursor + 1) if rows else 0
        elif key == term.PGUP:
            self.cursor = max(0, self.cursor - self.VISIBLE)
        elif key == term.PGDN:
            self.cursor = min(len(rows) - 1, self.cursor + self.VISIBLE) if rows else 0
        elif key == term.ENTER:
            self._descend(rows)
        elif key in (term.LEFT, "h"):
            self._go_up()
        elif key == "CTRL-D":
            self._on_choose(self.path)
            return True
        elif key == "\\" and self._on_drives:
            self._on_drives()
            return False  # 드라이브 창이 이 창을 대신한다
        elif key in (term.ESC, "q"):
            self._on_cancel()
            return True
        return False

    def _descend(self, rows):
        if not rows or not 0 <= self.cursor < len(rows):
            return
        name = rows[self.cursor]
        if name == "..":
            self._go_up()
            return
        if name == self.DRIVE_ROW:
            if self._on_drives:
                self._on_drives()
            return
        self.path = self.path / name
        self._load()

    def _go_up(self):
        if self.path != self.path.parent:
            self.path = self.path.parent
            self._load()


class PeerDialog(Dialog):
    """발견된 피어 목록. Enter 로 연결을 요청한다."""

    title = "연결할 PC 선택"

    def __init__(self, app):
        self.app = app
        self.cursor = 0
        self.peers = []
        self.waiting = None  # 승인을 기다리는 상대 이름

    def lines(self, inner_width):
        if self.waiting:
            return [
                term.BOLD + self.waiting + term.RESET + " 에게 연결을 요청했습니다.",
                "",
                term.FG_YELLOW + "상대가 승인할 때까지 기다리는 중..." + term.RESET,
                term.FG_GRAY + "최대 %d초" % config.APPROVAL_TIMEOUT + term.RESET,
            ]

        self.peers = self.app.discovery.peers() if self.app.discovery else []
        if not self.peers:
            return [
                term.FG_GRAY + "같은 네트워크에서 아직 아무도 찾지 못했습니다." + term.RESET,
                "",
                "상대 PC 에서도 MDirNet 을 실행했는지 확인하세요.",
                "방화벽이 UDP %d 를 막고 있을 수도 있습니다." % config.UDP_PORT,
            ]

        self.cursor = min(self.cursor, len(self.peers) - 1)
        out = []
        for index, peer in enumerate(self.peers):
            mark = term.REVERSE if index == self.cursor else ""
            out.append(
                mark + screen.pad("  %s   %s" % (peer.name, peer.host), inner_width) + term.RESET
            )
        out += ["", term.FG_GRAY + "Enter 연결 요청   Esc 취소" + term.RESET]
        return out

    def key(self, key):
        if self.waiting:
            return key in (term.ESC, "q")  # 기다리는 중엔 취소만 받는다

        if key in (term.UP, "k"):
            self.cursor = max(0, self.cursor - 1)
        elif key in (term.DOWN, "j"):
            self.cursor = min(len(self.peers) - 1, self.cursor + 1) if self.peers else 0
        elif key == term.ENTER and self.peers:
            peer = self.peers[self.cursor]
            self.waiting = peer.name
            self.app.request_connection(peer)
        elif key in (term.ESC, "q"):
            return True
        return False


class ProgressDialog(Dialog):
    """전송 진행률. 완료되면 앱이 닫는다."""

    title = "전송 중"

    def __init__(self, transfer):
        self.transfer = transfer

    def lines(self, inner_width):
        t = self.transfer
        bar_width = max(inner_width - 8, 10)
        filled = int(bar_width * t.ratio)
        bar = "#" * filled + "." * (bar_width - filled)
        return [
            screen.fit(t.label, inner_width),
            term.FG_GRAY + screen.fit(t.current, inner_width) + term.RESET,
            "",
            term.FG_GREEN + bar + term.RESET + " %3d%%" % int(t.ratio * 100),
            "%s / %s   %s"
            % (fsops.human_size(t.done), fsops.human_size(t.total), t.rate_text()),
            "",
            term.FG_GRAY
            + ("취소하는 중..." if t.cancelled else "Esc 취소")
            + term.RESET,
        ]

    def key(self, key):
        """Esc 로 취소를 요청한다. 창은 워커가 정리한 뒤 앱이 닫는다."""
        if key in (term.ESC, "CTRL-C") and not self.transfer.cancelled:
            self.transfer.cancelled = True
        return False


class App:
    """전체 애플리케이션. 이벤트 큐를 비우고 화면을 그리는 단일 루프."""

    def __init__(self, my_name, shares, tcp_port, udp_port, platform_name,
                 use_discovery=True, persist=True):
        self.my_name = my_name
        self.platform_name = platform_name
        self.events = queue.Queue()

        # 공유 폴더 목록. 비어 있으면 실행 직후 선택상자로 첫 항목을 받는다.
        # 하나라도 정해지기 전에는 아무것도 열지 않으므로 서비스 기동을 미룬다
        self.shares = shares
        self.persist = persist
        self.pick_root_at_start = len(shares) == 0

        self.local = Pane(self, is_remote=False)
        self.remote = Pane(self, is_remote=True)
        # 왼쪽은 첫 공유 폴더에서 시작한다. 목록이 비었으면 홈에서 시작한다
        first = next(iter(shares), None)
        self.local.path = first.path if first else Path.home()
        self.focus = self.local

        # 자리 이름. local/remote 는 '기본 역할' 이름이고, right 는 내 PC 를
        # 보도록 바꿀 수 있다. 왼쪽은 늘 내 PC 다 (바꾸지 않는다)
        self.left = self.local
        self.right = self.remote

        self.session = None
        self.modal = None
        self.status = "n 을 눌러 연결할 PC 를 찾습니다"
        self.transfer = None
        self.running = True
        self.show_hidden = False
        self._known_peers = set()  # 새로 나타난 상대를 알리기 위해
        self._services_started = False

        self.server = session.Server(my_name, platform_name, self.shares, self.events, tcp_port)
        self.port = self.server.bind()
        self.discovery = (
            discovery.Discovery(os.urandom(8).hex(), my_name, self.port, platform_name, udp_port)
            if use_discovery
            else None
        )
        self.renderer = screen.Renderer()
        self._body_height = 10  # 화면을 그리며 실제 높이로 갱신된다
        # Windows 에서만 방화벽을 묻는다. 다른 OS 는 물어볼 것이 없다
        self._firewall_asked = not winfw.available()

    # --- 실행 ---

    def run(self):
        if self.pick_root_at_start:
            # 공유 폴더가 하나도 없으면 서버를 열지 않는다. 취소하면 종료한다
            self._show_share_picker(
                Path.home(), on_cancel=self._quit, cancel_hint="Esc 종료", then_start=True
            )
        else:
            self.start_services()

        self.local.refresh()

        with term.Terminal() as terminal:
            while self.running:
                self._drain_events()
                self._notice_new_peers()
                # 공유 폴더 선택상자가 떠 있으면 그것이 끝난 뒤에 묻는다
                if not self._firewall_asked and self.modal is None:
                    self._ask_firewall()
                self.renderer.render(self._compose(term.size()), term.size())

                key = terminal.read_key(config.UI_TICK)
                if key is not None:
                    self._on_key(key)

        self.shutdown()

    def _ask_firewall(self):
        """Windows 에서 방화벽이 우리를 막고 있으면 한 번만 묻는다.

        규칙 변경은 관리자 권한이 필요하므로 조용히 할 수 없다. 동의하면 UAC
        창이 한 번 뜨고, 규칙이 들어가면 다음 실행부터는 묻지 않는다.
        """
        self._firewall_asked = True  # 거절하든 실패하든 다시 묻지 않는다
        try:
            state = winfw.inspect()
        except winfw.Unknown:
            return  # 상태를 모르면 붙잡지 않는다

        if state.ok:
            return

        body = ["상대가 나를 찾지 못하는 원인이 방화벽일 수 있습니다.", ""]
        if state.allow_missing:
            # 스크립트가 config 의 기본 포트로 규칙을 넣는다. --udp-port 로 바꿔
            # 실행했다면 그 포트는 직접 열어야 한다
            body.append(
                "  . 들어오는 TCP %d~%d 와 UDP %d 를 허용합니다"
                % (config.TCP_PORT, config.TCP_PORT + 9, config.UDP_PORT)
            )
        if state.blocks:
            # 차단 규칙은 허용 규칙보다 먼저 판정된다. 남겨두면 허용이 무의미하다
            body.append("  . 아래 차단 규칙을 지웁니다 (허용보다 먼저 적용됩니다)")
            body += ["      %s" % name for name in state.blocks[:5]]
            if len(state.blocks) > 5:
                body.append("      ~ 외 %d개" % (len(state.blocks) - 5))
        body += [
            "",
            term.FG_GRAY + "관리자 권한이 필요해 Windows 동의 창이 한 번 뜹니다." + term.RESET,
            term.FG_GRAY + "공용 네트워크 프로필은 열지 않습니다." + term.RESET,
            "",
            "지금 허용할까요?",
        ]

        def decide(ok):
            if not ok:
                self.status = "방화벽을 그대로 둡니다. tools/allow-firewall.ps1 로 나중에 할 수 있습니다"
                return
            if winfw.request(remove_blocks=bool(state.blocks)):
                self.status = "동의 창에서 허용하면 규칙이 들어갑니다"
            else:
                self.status = "방화벽 규칙을 넣지 못했습니다. 관리자 PowerShell 에서 직접 실행하세요"

        self.modal = ConfirmDialog("방화벽 허용", body, decide)

    def start_services(self):
        """TCP 수신과 브로드캐스트를 시작한다. 공유 폴더가 확정된 뒤에 부른다."""
        if self._services_started:
            return
        self._services_started = True
        self.server.start()
        if self.discovery:
            self.discovery.start()

    def _quit(self):
        self.running = False

    def _ask_quit(self):
        """연결 중이거나 전송 중이면 한 번 묻는다.

        다이얼로그마다 q 가 '닫기' 라서, 창을 닫은 직후 한 번 더 누르는 실수가
        곧 종료였다. NC 도 종료를 물었다.
        """
        if not self.session and not self.transfer:
            self.running = False
            return

        reason = "전송이 진행 중입니다." if self.transfer else "%s 와 연결되어 있습니다." % self.session.peer_name
        self.modal = ConfirmDialog(
            "종료",
            [reason, "", "정말 끝낼까요?"],
            lambda ok: self._quit() if ok else None,
        )

    def add_share(self, path):
        """목록에 폴더를 더한다. Server 가 같은 목록 객체를 보므로 곧바로 반영된다."""
        try:
            share = self.shares.add(path)
        except ValueError as exc:
            self.modal = MessageDialog("추가할 수 없습니다", str(exc), term.FG_RED)
            return None

        if self.persist:
            self._save_shares()
        self.status = "공유 폴더 추가: %s" % share.name
        return share

    def remove_share(self, name):
        if self.shares.remove(name) and self.persist:
            self._save_shares()
        self.status = "공유 폴더 제거: %s" % name

    def _save_shares(self):
        try:
            self.shares.save()
        except OSError as exc:
            self.status = "목록을 저장하지 못했습니다: %s" % exc

    def _show_help(self):
        self.modal = MessageDialog("도움말", _HELP_LINES)

    def _open_peer_dialog(self):
        self.modal = PeerDialog(self)

    def _open_menu(self):
        self.modal = MenuBar(self)

    def set_sort(self, pane, mode):
        """메뉴에서 정렬 기준을 직접 고른다."""
        pane.sort_mode = mode
        pane.entries = fsops.sort_entries(pane.entries, mode)
        pane._clamp()
        self.status = "%s 정렬: %s 기준" % (pane.label, fsops.SORT_LABELS[mode])

    def toggle_right_source(self):
        """오른쪽 패널을 내 PC / 상대 사이에서 바꾼다.

        왼쪽은 늘 내 PC 이므로 바꾸지 않는다. NC 의 Right 메뉴가 패널의 대상을
        고르는 것과 같은 자리다.
        """
        going_remote = not self.right.is_remote
        if going_remote and not self.session:
            self.status = "연결된 상대가 없습니다. n 으로 먼저 연결하세요"
            return

        self.right.set_remote(going_remote)
        self.status = "오른쪽 패널: %s" % ("상대" if going_remote else "내 PC")

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.status = "숨김 파일 %s" % ("보임" if self.show_hidden else "감춤")
        self.local.refresh()
        if self.session:
            self.remote.refresh()

    def _open_share_manager(self):
        """F4. 연결 중에도 열린다.

        `Session` 은 목록 객체를 그대로 들고 있으므로 여기서 더하거나 뺀 것이
        곧바로 상대의 요청 판정에 적용된다. 상대 화면은 다시 그릴 때까지
        낡은 목록을 보여주지만, 이미 없어진 공유로 들어가려 하면 거부된다.
        """
        if self.session:
            self.status = "연결 중입니다. 목록을 바꾸면 %s 에게 곧바로 적용됩니다" % (
                self.session.peer_name
            )
        self.modal = ShareDialog(self)

    def _show_share_picker(self, start, on_cancel, cancel_hint="Esc 취소", then_start=False):
        """공유 폴더로 더할 폴더를 고르는 선택상자. 드라이브 창과 엮어둔다."""

        def open_drives():
            # 드라이브를 고르면 그 루트에서 선택상자를 다시 연다
            self.modal = DriveDialog(
                fsops.list_drives(),
                lambda path: self._show_share_picker(path, on_cancel, cancel_hint, then_start),
            )

        def chosen(path):
            if self.add_share(path) is None:
                return
            if then_start:
                self.start_services()
                self.local.navigate(Path(path))
            else:
                self.modal = ShareDialog(self)

        self.modal = FolderDialog(
            start,
            on_choose=chosen,
            on_cancel=on_cancel,
            cancel_hint=cancel_hint,
            on_drives=open_drives,
        )

    def _open_drive_picker(self):
        """`\\` 키. 내 PC 패널을 다른 드라이브로 옮긴다.

        상대 패널에는 쓸 수 없다. 상대는 공유 루트 안에 갇혀 있어서 다른
        드라이브로 옮겨가도 곧바로 거부당한다. 헛되게 열지 않고 이유를 알린다.
        """
        pane = self.focus
        if pane.is_remote:
            self.status = "상대 쪽은 공유 폴더 안으로 제한됩니다. 드라이브를 바꿀 수 없습니다"
            return

        drives = fsops.list_drives()
        if len(drives) <= 1:
            self.status = "옮겨갈 드라이브가 없습니다"
            return
        self.modal = DriveDialog(drives, pane.navigate)

    def shutdown(self):
        if self.session:
            self.session.close("종료")
        self.server.stop()
        if self.discovery:
            self.discovery.stop()

    # --- 이벤트 ---

    def _notice_new_peers(self):
        """새 상대가 나타나면 알린다.

        헤더의 '발견 N대' 숫자만 조용히 바뀌면 사용자가 그 자리를 보고 있어야
        한다. 이 앱의 첫 동작이 연결인데 그 순간을 놓치면 안 된다.
        """
        if not self.discovery or self.session:
            return

        names = {p.name for p in self.discovery.peers()}
        fresh = names - self._known_peers
        self._known_peers = names

        if fresh and not self.modal:
            self.status = "%s 발견 - n 으로 연결하세요" % ", ".join(sorted(fresh))

    def _drain_events(self):
        while True:
            try:
                kind, *payload = self.events.get_nowait()
            except queue.Empty:
                return
            self._handle_event(kind, payload)

    def _handle_event(self, kind, payload):
        if kind == "approval":
            request = payload[0]
            if self.session:
                # 이미 연결된 상태에서는 새 요청을 받지 않는다. 상대에게 즉시 알린다
                request.decide(False)
                self.status = "%s 의 연결 요청을 거절했습니다 (이미 연결 중)" % request.name
            else:
                self.modal = ApprovalDialog(
                    request, deadline=time.monotonic() + config.APPROVAL_TIMEOUT
                )

        elif kind == "approval_expired":
            # 시간이 지나 이미 거부된 요청이다. 창을 닫고 이유를 알린다
            request = payload[0]
            if isinstance(self.modal, ApprovalDialog) and self.modal.request is request:
                self.modal = None
            self.status = "%s 의 연결 요청이 시간 초과로 취소되었습니다" % request.name

        elif kind == "connected":
            self._on_connected(payload[0])

        elif kind == "connect_failed":
            self.modal = MessageDialog("연결 실패", payload[0], term.FG_RED)

        elif kind == "disconnected":
            self._on_disconnected(payload[0])

        elif kind == "listing":
            self._pane(payload[0]).apply_listing(payload[1], payload[2], payload[3])

        elif kind == "list_error":
            self._pane(payload[0]).apply_list_error(payload[1], payload[2])

        elif kind == "received":
            self.status = "받음: %s" % Path(payload[0]).name
            self._refresh_local_panes(Path(payload[0]).parent)

        elif kind == "copy_cancelled":
            self.transfer = None
            if isinstance(self.modal, ProgressDialog):
                self.modal = None
            self.status = "전송을 취소했습니다 (이미 보낸 파일은 남아 있습니다)"
            self._refresh_panes()

        elif kind == "copy_done":
            self.transfer = None
            if isinstance(self.modal, ProgressDialog):
                self.modal = None
            count, errors = payload
            if errors:
                self.modal = MessageDialog(
                    "전송 완료 (오류 %d건)" % len(errors), errors[:8], term.FG_YELLOW
                )
            else:
                self.status = "%d개 항목을 전송했습니다" % count
            self._refresh_panes()

        elif kind == "view":
            name, data, size = payload
            self.status = ""
            self.modal = ViewerDialog(name, data, size)

        elif kind == "op_done":
            self.status = payload[0]
            self.focus.refresh()

        elif kind == "remote_changed":
            # 상대가 내 쪽 파일을 바꿨다. 그 폴더를 보고 있는 패널을 다시 읽는다
            self._refresh_local_panes(Path(payload[0]))

        elif kind == "error":
            self.modal = MessageDialog("오류", payload[0], term.FG_RED)

    def _refresh_panes(self):
        """두 패널을 갱신한다. 원격은 세션이 있을 때만, 로컬은 언제나.

        예전에는 `if self.session` 아래에 오른쪽 갱신을 두어서, 오른쪽이 내 PC 를
        보고 있고 연결이 없으면 복사한 파일이 화면에 안 나타났다.
        """
        for pane in (self.left, self.right):
            if pane.is_remote and not self.session:
                continue
            pane.refresh()

    def _refresh_local_panes(self, path):
        """상대가 건드린 폴더를 보고 있는 내 PC 패널만 다시 읽는다."""
        for pane in (self.left, self.right):
            if not pane.is_remote and Path(pane.path) == Path(path):
                pane.refresh()

    def _pane(self, side):
        return self.remote if side == "remote" else self.local

    def _on_connected(self, new_session):
        if self.session:
            new_session.close("이미 다른 연결이 있습니다")
            return

        self.session = new_session
        self.status = "%s 와 연결되었습니다" % new_session.peer_name
        if isinstance(self.modal, (PeerDialog, ApprovalDialog)):
            self.modal = None

        # 연결했다는 것은 상대를 보겠다는 뜻이다. 오른쪽을 상대로 돌린다
        self.right.is_remote = True

        def load_home():
            try:
                # 원격 경로는 공유 상대 경로 문자열이다. Path 로 감싸면
                # JSON 으로 직렬화되지 않아 요청이 조용히 실패한다
                self.remote.navigate(new_session.remote_home())
            except session.SessionError as exc:
                self.events.put(("error", "원격 폴더를 열 수 없습니다: %s" % exc))

        threading.Thread(target=load_home, name="remote-home", daemon=True).start()

    def _on_disconnected(self, reason):
        self.session = None
        self.transfer = None
        # 오른쪽이 내 PC 를 보고 있으면 끊겼다고 지울 이유가 없다
        if self.right.is_remote:
            self.right.entries = []
            self.right.selected.clear()
            self.focus = self.local
        if isinstance(self.modal, ProgressDialog):
            self.modal = None
        self.status = reason

    # --- 연결 ---

    def request_connection(self, peer):
        """워커 스레드에서 연결을 요청한다. 승인 대기 동안 UI 는 계속 반응한다."""

        def work():
            try:
                new_session = session.connect(
                    peer, self.my_name, self.platform_name, self.shares, self.events
                )
                self.events.put(("connected", new_session))
            except session.SessionError as exc:
                self.events.put(("connect_failed", str(exc)))
            except OSError as exc:
                self.events.put(("connect_failed", "연결할 수 없습니다: %s" % exc))

        threading.Thread(target=work, name="connect", daemon=True).start()

    # --- 키 처리 ---

    def _on_key(self, raw_key):
        # 한글 입력이 켜진 채로 y/n 을 누르면 'ㅛ'/'ㅜ' 가 들어와 단축키가 먹지 않는다.
        # 명령으로 쓸 때만 되돌린다. 입력창은 한글을 그대로 받아야 하므로 예외다
        # Ctrl-C 는 모달보다 먼저 본다. 전송 중에 갇히면 빠져나갈 길이 없다
        if raw_key == "CTRL-C":
            if self.transfer and not self.transfer.cancelled:
                self.transfer.cancelled = True
                self.status = "취소하는 중..."
                return
            self.running = False
            return

        if self.modal is not None:
            active = self.modal
            key = raw_key if isinstance(active, InputDialog) else term.command_key(raw_key)
            closed = active.key(key)
            # 다이얼로그가 콜백에서 다른 다이얼로그를 띄웠을 수 있다 (드라이브 >
            # 폴더 선택). 그때 None 을 덮어쓰면 방금 띄운 창이 사라진다
            if closed and self.modal is active:
                self.modal = None
            return

        key = term.command_key(raw_key)
        # 하단 바에 적힌 숫자를 그대로 누를 수 있게 한다. macOS 는 F 키에 fn 이
        # 필요하고, F 키의 이스케이프 시퀀스는 터미널마다 다르다. 숫자는 어느
        # OS.터미널에서나 ASCII 한 바이트로 와서 변형이 없고, 한글 입력 상태에도
        # 영향받지 않는다
        key = _DIGIT_FKEYS.get(key, key)

        if key in ("q", "F10"):
            self._ask_quit()
        elif key == term.TAB:
            self._toggle_focus()
        elif key in (term.UP, "k"):
            self.focus.move(-1)
        elif key in (term.DOWN, "j"):
            self.focus.move(1)
        elif key == term.PGUP:
            self.focus.move(-1, self._body_height)
        elif key == term.PGDN:
            self.focus.move(1, self._body_height)
        elif key == term.HOME_KEY:
            self.focus.cursor = 0
        elif key == term.END_KEY:
            self.focus.cursor = max(len(self.focus.rows()) - 1, 0)
        elif key in (term.SPACE, term.INSERT):
            self.focus.toggle_select()
        elif key == term.ENTER:
            self._enter()
        elif key in (term.LEFT, "h", term.BACKSPACE):
            # 맥 노트북의 delete 키는 Backspace 로 들어온다. 파일 관리자 관습대로
            # 상위 폴더에 묶는다
            self._go_up(self.focus)
        elif key in (term.RIGHT, "l"):
            self._enter_dir_only()
        elif key == "\\":
            self._open_drive_picker()
        elif key == "+":
            self._ask_mask(add=True)
        elif key in ("-", "_"):
            self._ask_mask(add=False)
        elif key in ("F5", "c"):
            self._start_copy()
        elif key == "F2":
            self.status = "%s 정렬: %s 기준" % (self.focus.label, self.focus.cycle_sort())
        elif key == "n":
            self._open_peer_dialog()
        elif key in ("F3", "v"):
            self._view_file()
        elif key in ("F4", "o"):
            self._open_share_manager()
        elif key == "F6":
            self._ask_rename()
        elif key == "d":
            self._disconnect()
        elif key in ("F7", "m"):
            self._ask_mkdir()
        elif key in ("F8", term.DELETE):
            self._ask_delete()
        elif key in ("F9", "/"):
            self._open_menu()
        elif key == "s":
            self.status = "%s 정렬: %s 기준" % (self.focus.label, self.focus.cycle_sort())
        elif key == "r":
            self.focus.refresh()
        elif key == ".":
            self.toggle_hidden()
        elif key == "=":
            self.toggle_right_source()
        elif key in ("F1", "?"):
            self._show_help()

    def _toggle_focus(self):
        if self.focus is self.left and self.right.active:
            self.focus = self.right
        else:
            self.focus = self.left

    def _enter(self):
        item = self.focus.current_item()
        if not item:
            return
        if item.get("up"):
            self._go_up(self.focus)
        elif item["is_dir"]:
            self.focus.navigate(self.focus.child(item["name"]))
        elif not self.focus.is_remote and fsops.is_executable(self.focus.child(item["name"])):
            self._run_file(item["name"])
        else:
            # 실행 파일이 아니면 보기다. 듀얼 패널 관습이고, 아무 반응이 없으면
            # 사용자는 키가 먹지 않았다고 생각한다
            self._view_file()

    def _enter_dir_only(self):
        """오른쪽 화살표. 폴더면 들어가고 파일이면 아무 일도 하지 않는다.

        `..` 도 무시한다. 위로 가는 것은 왼쪽 화살표의 몫이고, 오른쪽 화살표가
        위로 올라가면 방향과 어긋난다.
        """
        item = self.focus.current_item()
        if item and item["is_dir"] and not item.get("up"):
            self.focus.navigate(self.focus.child(item["name"]))

    def _go_up(self, pane):
        # 원격의 최상위는 공유 목록(가상 루트)이다. 그 위는 없다
        if not pane.at_top:
            pane.navigate(pane.up())

    def _disconnect(self):
        if not self.session:
            self.status = "연결된 상대가 없습니다"
            return
        self.session.close("연결을 끊었습니다")

    # --- 마스크 선택 ---

    def _ask_mask(self, add):
        pane = self.focus
        if not pane.active:
            return

        def apply(pattern):
            count = pane.select_mask(pattern, add=add)
            verb = "선택" if add else "해제"
            self.status = (
                "%s - %d개 %s" % (pattern, count, verb) if count else "%s - 맞는 항목이 없습니다" % pattern
            )

        self.modal = InputDialog(
            "선택 마스크" if add else "선택 해제 마스크",
            [
                "패턴에 맞는 항목을 한꺼번에 %s합니다." % ("선택" if add else "해제"),
                term.FG_GRAY + "예) *.txt   보고서*   *.jp*g" + term.RESET,
            ],
            apply,
            initial="*.",
        )

    # --- 원격 작업 공통 ---

    def _run_remote(self, work, on_done):
        """원격 요청은 응답을 기다리므로 반드시 워커 스레드에서 돌린다."""
        active = self.session

        def runner():
            try:
                work(active)
                self.events.put(("op_done", on_done))
            except (session.SessionError, OSError) as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=runner, name="remote-op", daemon=True).start()

    # --- 파일 실행 (Enter) ---

    def _run_file(self, name):
        """실행 권한이 있는 파일을 백그라운드로 띄운다.

        TUI 는 그대로 두므로 실행한 프로그램의 출력은 보이지 않는다. 대신 화면이
        뭉개지지 않는다. 원격 파일은 판단할 방법이 없어 여기까지 오지 않는다.
        """
        try:
            fsops.launch(self.focus.child(name))
        except OSError as exc:
            self.modal = MessageDialog("실행할 수 없습니다", str(exc), term.FG_RED)
            return
        self.status = "%s 를 실행했습니다" % name

    # --- 파일 보기 (F3) ---

    def _view_file(self):
        pane = self.focus
        item = pane.current_item()
        if not item or item.get("up"):
            return
        if item["is_dir"]:
            self.status = "폴더는 볼 수 없습니다. Enter 로 들어가세요"
            return

        path = pane.child(item["name"])
        limit = config.MAX_VIEW_BYTES

        if not pane.is_remote:
            try:
                data = fsops.read_head(path, limit)
            except OSError as exc:
                self.modal = MessageDialog("열 수 없습니다", str(exc), term.FG_RED)
                return
            self.modal = ViewerDialog(item["name"], data, item["size"])
            return

        # 원격 파일은 앞부분만 받아온다. 통째로 내려받지 않는다
        self.status = "%s 를 불러오는 중..." % item["name"]
        active = self.session

        def work():
            try:
                data, size = active.remote_head(path, limit)
                self.events.put(("view", item["name"], data, size))
            except session.SessionError as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=work, name="view", daemon=True).start()

    # --- 폴더 만들기 (F7) ---

    def _ask_mkdir(self):
        pane = self.focus
        if not pane.active:
            return
        if pane.is_remote and pane.at_top:
            self.status = "공유 목록에는 폴더를 만들 수 없습니다. 안으로 들어가세요"
            return

        def create(name):
            if "/" in name or "\\" in name:
                self.modal = MessageDialog("이름이 잘못되었습니다", "경로 구분자는 쓸 수 없습니다", term.FG_RED)
                return

            target = pane.child(name)
            if pane.is_remote:
                self._run_remote(
                    lambda s: s.remote_mkdir(target), "폴더를 만들었습니다: %s" % name
                )
                return
            try:
                target.mkdir()
                self.status = "폴더를 만들었습니다: %s" % name
                pane.refresh()
            except OSError as exc:
                self.modal = MessageDialog("만들 수 없습니다", str(exc), term.FG_RED)

        self.modal = InputDialog(
            "폴더 만들기",
            ["%s 안에 새 폴더를 만듭니다." % screen.fit_left(pane.location, 46), "", "이름을 입력하세요."],
            create,
        )

    # --- 이름 바꾸기 (F6) ---

    def _ask_rename(self):
        pane = self.focus
        if not pane.active:
            return

        item = pane.current_item()
        if not item or item.get("up"):
            self.status = "이름을 바꿀 항목에 커서를 두세요"
            return
        if pane.is_remote and pane.at_top:
            self.status = "공유 폴더 이름은 상대가 목록에서 정합니다"
            return

        old = item["name"]

        def apply(new_name):
            if new_name == old:
                return
            if "/" in new_name or "\\" in new_name or new_name in (".", ".."):
                self.modal = MessageDialog(
                    "이름이 잘못되었습니다", "경로 구분자는 쓸 수 없습니다", term.FG_RED
                )
                return

            if pane.is_remote:
                self._run_remote(
                    lambda active: active.remote_rename(pane.child(old), new_name),
                    "이름을 바꿨습니다: %s" % new_name,
                )
                return

            target = pane.path / new_name
            # 덮어쓰지 않는다. POSIX 의 rename 은 대상이 있으면 조용히 지운다
            if target.exists() or target.is_symlink():
                self.modal = MessageDialog(
                    "바꿀 수 없습니다", "같은 이름이 이미 있습니다: %s" % new_name, term.FG_RED
                )
                return
            try:
                (pane.path / old).rename(target)
                self.status = "이름을 바꿨습니다: %s" % new_name
                pane.selected.discard(old)
                pane.refresh()
            except OSError as exc:
                self.modal = MessageDialog("바꿀 수 없습니다", str(exc), term.FG_RED)

        self.modal = InputDialog(
            "이름 바꾸기",
            [
                term.FG_GRAY + screen.fit_left(pane.location, 46) + term.RESET,
                "",
                "%s 의 새 이름을 입력하세요." % old,
            ],
            apply,
            initial=old,
        )

    # --- 삭제 (F8) ---

    def _ask_delete(self):
        pane = self.focus
        if not pane.active:
            return
        if pane.is_remote and pane.at_top:
            self.status = "공유 폴더는 상대가 목록에서 빼야 합니다 (여기서는 못 지웁니다)"
            return

        items = pane.targets()
        if not items:
            self.status = "지울 항목을 고르세요 (Space 로 선택)"
            return

        where = self.session.peer_name if pane.is_remote else "내 PC"
        names = [i["name"] + ("/" if i["is_dir"] else "") for i in items]
        has_dir = any(i["is_dir"] for i in items)

        body = [
            term.BOLD + "%s 에서 %d개 항목을 지웁니다." % (where, len(items)) + term.RESET,
            "",
        ]
        body += [term.FG_YELLOW + "  " + n + term.RESET for n in names[:6]]
        if len(names) > 6:
            body.append(term.FG_GRAY + "  ~ 외 %d개" % (len(names) - 6) + term.RESET)
        body += [""]
        if has_dir:
            body.append(term.FG_RED + "폴더는 안에 든 것까지 함께 지워집니다." + term.RESET)
        body.append(term.FG_RED + "되돌릴 수 없습니다." + term.RESET)

        self.modal = ConfirmDialog(
            "삭제", body, lambda ok: self._do_delete(pane, items) if ok else None
        )

    def _do_delete(self, pane, items):
        paths = [pane.child(i["name"]) for i in items]

        if pane.is_remote:
            def work(active):
                for path in paths:
                    active.remote_delete(path)

            self._run_remote(work, "%d개 항목을 지웠습니다" % len(paths))
            return

        failed = []
        for path in paths:
            try:
                fsops.remove_tree(path)
            except OSError as exc:
                failed.append("%s - %s" % (path.name, exc))

        pane.selected.clear()
        pane.refresh()
        if failed:
            self.modal = MessageDialog("일부를 지우지 못했습니다", failed[:8], term.FG_YELLOW)
        else:
            self.status = "%d개 항목을 지웠습니다" % len(paths)

    # --- 복사 ---

    def _start_copy(self):
        if self.transfer:
            self.status = "이미 전송 중입니다"
            return

        source, dest = self.focus, (self.right if self.focus is self.left else self.left)

        # 양쪽 모두 내 PC 라면 세션이 필요없다. 그냥 로컬 복사다
        needs_session = source.is_remote or dest.is_remote
        if needs_session and not self.session:
            self.status = "먼저 n 으로 상대에 연결하세요"
            return
        if source is dest:
            self.status = "같은 패널로는 복사할 수 없습니다"
            return

        # 가상 루트에는 파일을 둘 수 없다. 파일마다 오류를 내는 것보다
        # 미리 알려주는 편이 낫다
        if dest.is_remote and dest.at_top:
            self.status = "상대의 공유 폴더 안으로 들어가세요 (Enter)"
            return
        if source.is_remote and source.at_top:
            self.status = "공유 폴더 안으로 들어가 파일을 고르세요 (Enter)"
            return

        items = source.targets()
        if not items:
            self.status = "복사할 항목을 고르세요 (Space 로 선택)"
            return

        names = ", ".join(i["name"] for i in items[:3]) + (" 외" if len(items) > 3 else "")

        body = [
            "%s > %s" % (source.label, dest.label),
            "",
            screen.fit(names, 50),
            "",
            term.FG_GRAY + "대상: %s" % dest.location + term.RESET,
        ]

        # 매번 뜨는 경고는 아무도 읽지 않는다. 실제로 겹칠 때만 알린다
        clashes = self._clashing_names(dest, items)
        if clashes:
            body.append(
                term.FG_RED
                + "기존 %d개를 덮어씁니다: %s"
                % (len(clashes), screen.fit(", ".join(clashes), 36))
                + term.RESET
            )

        self.modal = ConfirmDialog(
            "복사", body, lambda ok: self._run_copy(source, dest, items) if ok else None
        )

    @staticmethod
    def _clashing_names(dest, items):
        """대상에 이미 있는 이름들. 추가 왕복 없이 지금 아는 것만으로 판정한다.

        원격은 화면에 띄운 목록을, 로컬은 파일시스템을 본다. 원격 목록이 살짝
        낡았을 수는 있지만 경고를 띄우기 위한 것이라 그 정도면 충분하다.
        """
        existing = {e["name"] for e in dest.entries}
        if not dest.is_remote:
            existing |= {
                i["name"] for i in items if (Path(dest.path) / i["name"]).exists()
            }
        return [i["name"] for i in items if i["name"] in existing]

    def _run_copy(self, source, dest, items):
        """전송 계획을 세우고 워커 스레드에서 실행한다."""
        transfer = Transfer("%d개 항목" % len(items), 0)
        self.transfer = transfer
        self.modal = ProgressDialog(transfer)
        active = self.session

        def work():
            errors = []
            try:
                plan, dirs = self._build_plan(source, items, active)
                transfer.total = sum(size for _, _, size in plan) or 1

                # 빈 폴더까지 만들어준다. 파일만 옮기면 빈 폴더가 사라진다
                for rel in dirs:
                    target = dest.child(rel)
                    try:
                        if dest.is_remote:
                            active.remote_mkdir(target)
                        else:
                            Path(target).mkdir(parents=True, exist_ok=True)
                    except (session.SessionError, OSError):
                        pass  # 이미 있으면 그만이다

                for rel, src_path, size in plan:
                    if transfer.cancelled:
                        break
                    transfer.current = rel
                    dest_path = dest.child(rel)
                    try:
                        if source.is_remote:
                            active.pull_file(
                                src_path, dest_path, on_progress=_adder(transfer)
                            )
                        elif dest.is_remote:
                            active.push_file(
                                src_path, dest_path, on_progress=_adder(transfer)
                            )
                        else:
                            # 내 PC 안에서의 복사. 네트워크를 거치지 않는다
                            fsops.copy_file(
                                src_path, dest_path, on_progress=_adder(transfer)
                            )
                    except (session.SessionError, OSError) as exc:
                        errors.append("%s - %s" % (rel, exc))
                if transfer.cancelled:
                    self.events.put(("copy_cancelled", None))
                else:
                    self.events.put(("copy_done", len(plan), errors))
            except (session.SessionError, OSError) as exc:
                self.events.put(("copy_done", 0, ["%s" % exc]))

        threading.Thread(target=work, name="copy", daemon=True).start()

    def _build_plan(self, source, items, active):
        """전송할 파일을 (상대경로, 원본경로, 크기) 목록으로 펼친다.

        디렉터리는 여기서 재귀적으로 펼쳐진다. 목적지 쪽은 파일을 받을 때
        부모 디렉터리를 자동으로 만들기 때문에 디렉터리 자체는 보내지 않는다.
        """
        plan, dirs = [], []
        for item in items:
            name = item["name"]
            src = source.child(name)

            if not item["is_dir"]:
                plan.append((name, src, item["size"]))
                continue

            # 디렉터리는 여기서 펼친다. tree 는 상대 경로 목록을 돌려주므로
            # 원본 쪽 경로 공간에 맞춰 다시 붙인다
            if source.is_remote:
                listing, subdirs = active.remote_tree(src)
                join = lambda base, rel: shares_mod.join(base, rel)
            else:
                listing, subdirs = fsops.walk_tree(src), fsops.walk_dirs(src)
                join = lambda base, rel: base / rel

            # 폴더 자체와 하위 폴더를 먼저 적어둔다. 파일이 하나도 없는 폴더도
            # 복사되어야 한다. 이걸 빼면 빈 폴더를 복사했을 때 아무 일도 안 난다
            dirs.append(name)
            dirs += ["%s/%s" % (name, d) for d in subdirs]

            for entry in listing:
                plan.append(
                    ("%s/%s" % (name, entry["path"]), join(src, entry["path"]), entry["size"])
                )
        return plan, dirs

    # --- 렌더링 ---

    def _compose(self, size):
        cols, rows = size
        # 머리글 1 + 패널 + 상태줄 1 + 힌트줄 1 = 화면 높이
        pane_height = max(rows - 3, 4)

        lines = [self._header(cols)]
        lines += self._panes(cols, pane_height)
        lines.append(self._status(cols))
        lines.append(self._fkey_bar(cols))

        if self.modal is not None:
            # 메뉴바는 제목 줄과 그 아래 상자를 직접 놓는다 (가운데 정렬이 아니다)
            if hasattr(self.modal, "render_into"):
                self.modal.render_into(lines, cols, rows)
            else:
                self._overlay(lines, cols, rows)
        return lines

    def _header(self, cols):
        left = " %s  %s" % (term.BOLD + "MDirNet" + term.RESET, self.my_name)

        # 좁은 터미널에서도 연결 상태는 보여야 한다. 긴 형태부터 시도해
        # 들어가는 것을 고른다
        if self.session:
            color, variants = term.FG_GREEN, [
                "* %s 연결됨" % self.session.peer_name,
                "* %s" % self.session.peer_name,
                "*",
            ]
        else:
            found = len(self.discovery.peers()) if self.discovery else 0
            color, variants = term.FG_GRAY, [
                "o 연결 없음 . 발견 %d대 . 포트 %d" % (found, self.port),
                "o 발견 %d대" % found,
                "o",
            ]

        room = cols - screen.width(left) - 2
        text = next((v for v in variants if screen.width(v) <= room), "")
        gap = max(cols - screen.width(left) - screen.width(text) - 1, 0)
        return screen.pad(left + " " * gap + color + text + term.RESET, cols)

    def _panes(self, cols, height):
        """MDir 처럼 바깥 벽 없이 가로줄과 가운데 구분선만으로 나눈다.

        위아래가 각각 제목 줄과 가로줄, 그리고 가로줄과 요약 줄이다.
        테두리에 제목을 끼워넣던 방식보다 읽기 쉽고 참고 화면과도 같다.
        바깥 벽을 없앤 만큼 두 칸이 내용으로 돌아간다.

            내 PC  ~/Documents              | office-pc  /프로젝트
            ================================+========================
              ..            [ Up-Dir ]      |   ..          [ Up-Dir ]
            ================================+========================
            3 File 2 Dir 20 KB . 이름순     | 2 File 1 Dir 13 KB
        """
        content = cols - 1  # 가운데 구분선 한 칸만 뺀다
        left_width = content // 2
        right_width = content - left_width

        left, right = self.left, self.right
        body_height = max(height - 4, 1)  # 제목 + 가로줄 + 본문 + 가로줄 + 요약
        self._body_height = body_height  # PgUp/PgDn 이 이 값을 쓴다
        for pane in (left, right):
            pane.scroll_into_view(body_height)

        wall = term.FG_GRAY + BOX_V + term.RESET
        rule = (
            term.FG_GRAY
            + PANE_RULE * left_width
            + "+"
            + PANE_RULE * right_width
            + term.RESET
        )

        out = [
            self._pane_head(left, left_width) + wall + self._pane_head(right, right_width),
            rule,
        ]
        for index in range(body_height):
            out.append(
                self._pane_row(left, index, left_width)
                + wall
                + self._pane_row(right, index, right_width)
            )
        out.append(rule)
        out.append(
            self._pane_foot(left, left_width) + wall + self._pane_foot(right, right_width)
        )
        return out

    def _pane_head(self, pane, width):
        """패널 제목 줄. 초점이 있으면 반전시킨다.

        테두리 색으로 초점을 보일 수 없으므로(테두리가 없다) 제목을 강조한다.
        NC 와 MDir 모두 활성 패널의 머리를 강조했다.
        """
        focused = pane is self.focus
        label = pane.label
        room = width - 2 - screen.width(label) - 2
        title = "%s  %s" % (label, screen.fit_left(pane.location, max(room, 0)))

        style = term.REVERSE if focused else term.BOLD
        return style + screen.pad(title, width) + term.RESET

    def _pane_row(self, pane, index, width):
        rows = pane.rows()
        position = pane.offset + index

        if position >= len(rows):
            # 항목이 없으면 본문이 통째로 백지라 '로딩 중' 과 구분되지 않는다
            if pane.active and not pane.entries and not pane.message and position == len(rows):
                return term.FG_GRAY + screen.pad(" (빈 폴더)", width) + term.RESET
            return " " * width

        return self._entry_line(pane, rows[position], position, width, pane is self.focus)

    def _pane_foot(self, pane, width):
        text = pane.message or self._pane_summary(pane)
        return term.FG_GRAY + screen.pad(screen.fit(text, width), width) + term.RESET

    @staticmethod
    def _columns(inner):
        """보여줄 칸을 정한다.

        보통은 모든 칸을 그대로 둔다. 이름 칸이 남는 자리를 가져가므로 창 폭이
        달라져도 오른쪽 칸들의 자리는 변하지 않는다.

        다만 이름에 최소 %d칸도 못 줄 만큼 좁으면(전체 폭 82칸 아래) 물리적으로
        다 담을 수 없다. 그때만 시간, 날짜, 확장자 순으로 접는다.
        """ % NAME_MIN
        if inner >= FIXED_COLS + NAME_MIN:
            return True, True, True

        without_time = FIXED_COLS - (TIME_COL + 1)
        if inner >= without_time + NAME_MIN:
            return True, True, False

        without_date = without_time - (DATE_COL + 1)
        if inner >= without_date + NAME_MIN:
            return True, False, False
        return False, False, False

    def _entry_line(self, pane, item, position, inner, focused):
        is_cursor = position == pane.cursor
        picked = item["name"] in pane.selected and not item.get("up")
        is_up = bool(item.get("up"))

        show_ext, show_date, show_time = self._columns(inner)

        # 크기 칸에는 디렉터리면 MDir 처럼 [ SubDir ] / [ Up-Dir ] 이 들어간다
        if is_up:
            size_text = "[ Up-Dir ]"
        elif item["is_dir"]:
            size_text = "[ SubDir ]"
        else:
            # GB/MB/KB 로 보여준다. 바이트 수를 그대로 쓰면 칸이 넓어져
            # 그만큼 이름이 짧아진다
            size_text = fsops.human_size(item["size"])

        if show_ext:
            base, ext = ("..", "") if is_up else fsops.split_name(item["name"])
            if item["is_dir"]:
                base, ext = item["name"], ""  # 폴더는 이름을 쪼개지 않는다
        else:
            base, ext = item["name"], ""

        # 선택 표시 한 칸 + 고정 칸들. 양쪽 패널 모두 0번 칸에서 시작한다
        used = 1 + SIZE_COL + (EXT_COL + 1 if show_ext else 0)
        used += (DATE_COL + 1) if show_date else 0
        used += (TIME_COL + 1) if show_time else 0
        name_width = max(inner - used, NAME_MIN)

        parts = ["*" if picked else " ", screen.pad(base, name_width)]
        if show_ext:
            parts.append(" " + screen.pad(ext.upper(), EXT_COL))
        parts.append(screen.rjust(size_text, SIZE_COL))
        if show_date:
            parts.append(" " + screen.rjust(fsops.mdir_date(item.get("mtime")), DATE_COL))
        if show_time:
            parts.append(" " + screen.rjust(fsops.mdir_time(item.get("mtime")), TIME_COL))

        text = screen.pad("".join(parts), inner)

        if is_cursor and focused:
            return term.REVERSE + text + term.RESET
        if is_cursor:
            return term.BOLD + text + term.RESET
        if picked:
            return term.FG_YELLOW + text + term.RESET
        if item["is_dir"]:
            return term.FG_CYAN + text + term.RESET
        # MDir 은 실행 파일을 따로 물들였다
        if ext.lower() in ("exe", "com", "bat", "sh", "app"):
            return term.FG_GREEN + text + term.RESET
        return text

    def _pane_summary(self, pane):
        if not pane.active:
            return "n 으로 연결하세요"
        if pane.selected:
            total = sum(e["size"] for e in pane.entries if e["name"] in pane.selected)
            return "%d개 선택 . %s" % (len(pane.selected), fsops.human_size(total))

        # MDir 하단처럼 파일 수 / 폴더 수 / 전체 바이트를 보여준다
        files = [e for e in pane.entries if not e["is_dir"]]
        dirs = [e for e in pane.entries if e["is_dir"]]
        summary = "%d File  %d Dir  %s Byte  . %s순" % (
            len(files),
            len(dirs),
            fsops.mdir_size(sum(e["size"] for e in files)),
            fsops.SORT_LABELS[pane.sort_mode],
        )

        # C:\ 에서는 '..' 이 없어 더 올라갈 곳이 안 보인다. 나갈 길을 알려준다
        if not pane.is_remote and fsops.is_filesystem_root(pane.path):
            return summary + "   \\ 드라이브"
        return summary

    def _status(self, cols):
        return screen.pad(" " + self.status, cols)

    def _fkey_bar(self, cols):
        """MDir.Norton Commander 특유의 하단 기능키 줄.

        칸 하나가 7칸보다 좁아지면 라벨이 잘려 읽을 수 없으므로, 그때는
        짧은 안내 문구로 물러난다.
        """
        cell = cols // len(_FKEYS)
        if cell < 7:
            # 손으로 적으면 키를 옮길 때마다 어긋난다. 늘 _FKEYS 에서 만든다
            picked = [n + l for n, l in _FKEYS if n in ("1", "2", "3", "5", "9", "0")]
            return term.FG_GRAY + screen.pad(" " + " ".join(picked), cols) + term.RESET

        parts = []
        for number, label in _FKEYS:
            # 번호는 흐리게, 라벨은 반전. 옛 화면의 그 모양이다
            parts.append(
                term.FG_GRAY
                + number
                + term.RESET
                + term.REVERSE
                + screen.pad(label, cell - len(number) - 1)
                + term.RESET
                + " "
            )
        return screen.pad("".join(parts), cols)

    def _overlay(self, lines, cols, rows):
        """모달을 화면 가운데 띄운다.

        screen.splice() 로 겹쳐 쓰므로 상자 좌우의 패널 테두리가 그대로 남는다.
        """
        box_width = min(max(cols - 8, 30), 96)
        inner = box_width - 4
        self.modal.max_body = max(rows - 8, 5)
        body = self.modal.lines(inner)

        content = [
            term.BOLD + screen.fit(self.modal.title, inner) + term.RESET,
            term.FG_GRAY + BOX_H * inner + term.RESET,
        ] + body

        box = [BOX_TL + BOX_H * (box_width - 2) + BOX_TR]
        for line in content:
            box.append(BOX_V + " " + screen.pad(line, inner) + " " + BOX_V)
        box.append(BOX_BL + BOX_H * (box_width - 2) + BOX_BR)

        left = max((cols - box_width) // 2, 0)
        start = max((rows - len(box)) // 2, 0)
        for index, line in enumerate(box):
            row = start + index
            if 0 <= row < rows:
                while len(lines) <= row:
                    lines.append("")
                lines[row] = screen.pad(
                    screen.splice(lines[row], left, term.FG_CYAN + line + term.RESET), cols
                )


# 상단 숫자열 > 기능키. 키패드는 application 모드에서 이스케이프 시퀀스를 보내므로
# 여기에 넣지 않는다 (숫자열만 다룬다)
_DIGIT_FKEYS = {
    "1": "F1",
    "2": "F2",
    "3": "F3",
    "4": "F4",
    "5": "F5",
    "6": "F6",
    "7": "F7",
    "8": "F8",
    "9": "F9",
    "0": "F10",
}

# NC 의 10칸 배치에 최대한 맞춘다. 연결과 끊기는 Commands 메뉴와 문자 키(n/d)로만
# 두고, 6번은 NC 의 Rename/Move 자리를 되찾았다
_FKEYS = [
    ("1", "도움"),
    ("2", "정렬"),
    ("3", "보기"),
    ("4", "폴더"),
    ("5", "복사"),
    ("6", "이름"),
    ("7", "만들"),
    ("8", "삭제"),
    ("9", "메뉴"),
    ("0", "끝내"),
]


def _adder(transfer):
    """진행률 콜백. 받은 바이트 수를 누적한다."""

    def add(n):
        transfer.done += n

    return add


_HELP_LINES = [
    "왼쪽은 늘 내 PC 입니다. 오른쪽은 상대 또는 내 PC 를 볼 수 있습니다 (=).",
    "양쪽이 모두 내 PC 면 복사는 네트워크를 거치지 않습니다.",
    "",
    "Tab           패널 전환",
    "^ v k j       커서 이동    PgUp/PgDn 페이지    Home/End 처음과 끝",
    "Enter         디렉터리 열기 (.. 은 상위로)",
    "              실행 권한이 있는 파일이면 실행, 그 밖의 파일은 보기",
    "< h           상위 디렉터리        > l   폴더 안으로",
    "Space, Insert 선택 토글",
    "+  -          마스크로 한꺼번에 선택 / 해제  (예: *.txt)",
    "\\             드라이브 / 볼륨 바꾸기 (내 PC 쪽만)",
    "",
    "기능키는 하단 바에 적힌 숫자를 그대로 눌러도 됩니다.",
    "맥에서는 F 키에 fn 이 필요하니 숫자가 편합니다.",
    "",
    "1 . F1  도움말              2 . F2  정렬 바꾸기 (s)",
    "3 . F3  파일 보기 (v)       4 . F4  공유 폴더 관리 (o)",
    "5 . F5  복사 (c)            6 . F6  이름 바꾸기",
    "7 . F7  폴더 만들기 (m)     8 . F8  삭제 (Delete)",
    "9 . F9  상단 메뉴 (/)       0 . F10 종료 (q)",
    "",
    "n             연결할 PC 찾기      d   연결 끊기",
    "=             오른쪽 패널을 내 PC / 상대 로 전환",
    ".             숨김 파일 보기/감추기",
    "r             새로고침            \\   드라이브",
    "",
    "연결, 끊기, 새로고침, 드라이브는 상단 메뉴(9)에도 있습니다.",
    "",
    "정렬은 이름 > 확장자 > 크기 > 날짜 순으로 돌아갑니다.",
    "Windows 에서 C:\\ 위로는 못 올라가므로 \\ 로 드라이브를 고릅니다.",
    "복사와 삭제는 선택한 항목이 없으면 커서에 있는 것 하나를 씁니다.",
    "상대가 연결을 요청하면 승인 창이 자동으로 뜹니다.",
]
