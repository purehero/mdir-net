# 듀얼 패널 상태 기계, 키 바인딩, 다이얼로그를 담은 UI 본체

import fnmatch
import os
import queue
import threading
from pathlib import Path

from .. import config, discovery, fsops, session, shares as shares_mod
from . import screen, term

# 테두리 문자. 폭이 정확히 1칸이고 East Asian Width 가 A(Ambiguous)가 아닌 것만
# 쓴다. 박스 문자(U+2500~257F) 112자는 전부 Ambiguous 라 한글 터미널에서 두 칸으로
# 그려져 화면을 밀어버린다. 모서리 전용 문자(U+231C~231F)는 EAW 가 N 이라 안전하다
BOX_H, BOX_V = "-", "|"
BOX_TL, BOX_TR, BOX_BL, BOX_BR = "\u231c", "\u231d", "\u231e", "\u231f"

SIZE_COL = 10  # 크기를 표시할 우측 열 폭


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

    @property
    def ratio(self):
        return min(self.done / self.total, 1.0) if self.total else 0.0


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
                self.entries = fsops.sort_entries(fsops.list_dir(self.path), self.sort_mode)
                self.message = ""
            except OSError as exc:
                self.entries = []
                self.message = "읽을 수 없습니다: %s" % exc
            self._clamp()
            return

        self.message = "불러오는 중~"
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

    def apply_listing(self, generation, path, entries):
        if generation != self._generation:
            return  # 그 사이 다른 디렉터리로 옮겨갔다. 낡은 결과는 버린다
        self.path = path if self.is_remote else Path(path)
        self.entries = fsops.sort_entries(entries, self.sort_mode)
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
        self._prompt = prompt if isinstance(prompt, list) else [prompt]
        self._on_submit = on_submit

    def lines(self, inner):
        return (
            self._prompt
            + ["", term.BOLD + screen.pad(self.text + "_", inner) + term.RESET, ""]
            + [term.FG_GRAY + "Enter 확인   Esc 취소" + term.RESET]
        )

    def key(self, key):
        # q 도 입력해야 하므로 취소는 Esc 뿐이다
        if key == term.ENTER:
            if self.text:
                self._on_submit(self.text)
            return True
        if key == term.ESC:
            return True
        if key == term.BACKSPACE:
            self.text = self.text[:-1]
        elif key == term.SPACE:
            self.text += " "
        elif len(key) == 1 and key.isprintable():
            self.text += key
        return False


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
    """오류와 안내 표시. 아무 키나 누르면 닫힌다."""

    def __init__(self, title, body, color=""):
        self.title = title
        self._body = body if isinstance(body, list) else [body]
        self._color = color

    def lines(self, inner_width):
        return [self._color + line + term.RESET for line in self._body] + [
            "",
            term.FG_GRAY + "아무 키나 누르면 닫힙니다" + term.RESET,
        ]

    def key(self, key):
        return True


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

    def __init__(self, request):
        self.request = request
        self.accept = True  # 커서가 놓인 버튼. 흔한 경우가 수락이므로 여기서 시작한다

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
            term.FG_GRAY + "< > 선택   Enter 확정   Esc 거부" + term.RESET,
        ]

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
            self.app._show_share_picker(start, on_cancel=lambda: None)
            return False  # 선택상자가 이 창을 대신한다
        elif key in ("d", term.DELETE) and shares:
            self._confirm_remove(shares[self.cursor])
            return False
        elif key in (term.ESC, "q"):
            return True
        return False

    def _confirm_remove(self, share):
        def decide(ok):
            if ok:
                self.app.remove_share(share.name)
                self.cursor = max(0, self.cursor - 1)
            self.app.modal = ShareDialog(self.app)
            self.app.modal.cursor = self.cursor

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
            term.BOLD + screen.fit(str(self.path), inner) + term.RESET,
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
                term.FG_YELLOW + "상대가 승인할 때까지 기다리는 중~" + term.RESET,
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
            "%s / %s" % (fsops.human_size(t.done), fsops.human_size(t.total)),
        ]

    def key(self, key):
        return False  # 전송 중에는 닫지 않는다


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

        self.session = None
        self.modal = None
        self.status = "F2 를 눌러 연결할 PC 를 찾습니다"
        self.transfer = None
        self.running = True
        self._services_started = False

        self.server = session.Server(my_name, platform_name, self.shares, self.events, tcp_port)
        self.port = self.server.bind()
        self.discovery = (
            discovery.Discovery(os.urandom(8).hex(), my_name, self.port, platform_name, udp_port)
            if use_discovery
            else None
        )
        self.renderer = screen.Renderer()

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
                self.renderer.render(self._compose(term.size()), term.size())

                key = terminal.read_key(config.UI_TICK)
                if key is not None:
                    self._on_key(key)

        self.shutdown()

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

    def _open_share_manager(self):
        """F4. 연결 중에는 막는다.

        Session 은 만들어질 때 목록 객체를 받으므로 항목을 더하면 반영되지만,
        상대는 이미 가상 루트를 화면에 띄운 상태다. 목록이 바뀌었는데 상대
        화면은 그대로여서 혼란스럽다. 끊은 뒤에 바꾸게 한다.
        """
        if self.session:
            self.status = "연결 중에는 바꿀 수 없습니다. F6 으로 끊고 다시 시도하세요"
            return
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
        if self.focus is self.remote:
            self.status = "상대 쪽은 공유 폴더 안으로 제한됩니다. 드라이브를 바꿀 수 없습니다"
            return

        drives = fsops.list_drives()
        if len(drives) <= 1:
            self.status = "옮겨갈 드라이브가 없습니다"
            return
        self.modal = DriveDialog(drives, self.local.navigate)

    def shutdown(self):
        if self.session:
            self.session.close("종료")
        self.server.stop()
        if self.discovery:
            self.discovery.stop()

    # --- 이벤트 ---

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
                self.modal = ApprovalDialog(request)

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
            if not self.local.is_remote:
                self.local.refresh()

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
            self.local.refresh()
            if self.session:
                self.remote.refresh()

        elif kind == "view":
            name, data, size = payload
            self.status = ""
            self.modal = ViewerDialog(name, data, size)

        elif kind == "op_done":
            self.status = payload[0]
            self.focus.refresh()

        elif kind == "remote_changed":
            # 상대가 내 쪽 파일을 지웠다. 왼쪽 패널을 다시 읽어 반영한다
            if Path(payload[0]) == self.local.path:
                self.local.refresh()

        elif kind == "error":
            self.modal = MessageDialog("오류", payload[0], term.FG_RED)

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
        self.remote.entries = []
        self.remote.selected.clear()
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

        if key in ("q", "F10", "CTRL-C"):
            self.running = False
        elif key == term.TAB:
            self._toggle_focus()
        elif key in (term.UP, "k"):
            self.focus.move(-1)
        elif key in (term.DOWN, "j"):
            self.focus.move(1)
        elif key == term.PGUP:
            self.focus.move(-1, 10)
        elif key == term.PGDN:
            self.focus.move(1, 10)
        elif key == term.HOME_KEY:
            self.focus.cursor = 0
        elif key == term.END_KEY:
            self.focus.cursor = max(len(self.focus.rows()) - 1, 0)
        elif key in (term.SPACE, term.INSERT):
            self.focus.toggle_select()
        elif key == term.ENTER:
            self._enter()
        elif key in (term.LEFT, "h"):
            self._go_up(self.focus)
        elif key == "\\":
            self._open_drive_picker()
        elif key in ("+", "="):
            self._ask_mask(add=True)
        elif key in ("-", "_"):
            self._ask_mask(add=False)
        elif key in ("F5", "c"):
            self._start_copy()
        elif key in ("F2", "n"):
            self.modal = PeerDialog(self)
        elif key in ("F3", "v"):
            self._view_file()
        elif key in ("F4", "o"):
            self._open_share_manager()
        elif key in ("F6", "d"):
            self._disconnect()
        elif key in ("F7", "m"):
            self._ask_mkdir()
        elif key in ("F8", term.DELETE):
            self._ask_delete()
        elif key in ("F9", "s"):
            self.status = "정렬: %s 기준" % self.focus.cycle_sort()
        elif key == "r":
            self.focus.refresh()
        elif key in ("F1", "?"):
            self.modal = MessageDialog("도움말", _HELP_LINES)

    def _toggle_focus(self):
        if self.focus is self.local and self.session:
            self.focus = self.remote
        else:
            self.focus = self.local

    def _enter(self):
        item = self.focus.current_item()
        if not item:
            return
        if item.get("up"):
            self._go_up(self.focus)
        elif item["is_dir"]:
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
        self.status = "%s 를 불러오는 중~" % item["name"]
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
            ["%s 안에 새 폴더를 만듭니다." % screen.fit(str(pane.path), 46), "", "이름을 입력하세요."],
            create,
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

        source, dest = self.focus, (self.remote if self.focus is self.local else self.local)
        if not self.session:
            self.status = "먼저 F2 로 상대에 연결하세요"
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
        self.modal = ConfirmDialog(
            "복사",
            [
                "%s > %s" % (source.title.split("  ")[0], dest.title.split("  ")[0]),
                "",
                screen.fit(names, 50),
                "",
                term.FG_GRAY + "대상: %s" % dest.location + term.RESET,
                term.FG_YELLOW + "같은 이름이 있으면 덮어씁니다." + term.RESET,
            ],
            lambda ok: self._run_copy(source, dest, items) if ok else None,
        )

    def _run_copy(self, source, dest, items):
        """전송 계획을 세우고 워커 스레드에서 실행한다."""
        transfer = Transfer("%d개 항목" % len(items), 0)
        self.transfer = transfer
        self.modal = ProgressDialog(transfer)
        active = self.session

        def work():
            errors = []
            try:
                plan = self._build_plan(source, items, active)
                transfer.total = sum(size for _, _, size in plan) or 1

                for rel, src_path, size in plan:
                    transfer.current = rel
                    dest_path = dest.child(rel)
                    try:
                        if source.is_remote:
                            active.pull_file(
                                src_path, dest_path, on_progress=_adder(transfer)
                            )
                        else:
                            active.push_file(
                                src_path, dest_path, on_progress=_adder(transfer)
                            )
                    except (session.SessionError, OSError) as exc:
                        errors.append("%s - %s" % (rel, exc))
                self.events.put(("copy_done", len(plan), errors))
            except (session.SessionError, OSError) as exc:
                self.events.put(("copy_done", 0, ["%s" % exc]))

        threading.Thread(target=work, name="copy", daemon=True).start()

    def _build_plan(self, source, items, active):
        """전송할 파일을 (상대경로, 원본경로, 크기) 목록으로 펼친다.

        디렉터리는 여기서 재귀적으로 펼쳐진다. 목적지 쪽은 파일을 받을 때
        부모 디렉터리를 자동으로 만들기 때문에 디렉터리 자체는 보내지 않는다.
        """
        plan = []
        for item in items:
            name = item["name"]
            src = source.child(name)

            if not item["is_dir"]:
                plan.append((name, src, item["size"]))
                continue

            # 디렉터리는 여기서 펼친다. tree 는 상대 경로 목록을 돌려주므로
            # 원본 쪽 경로 공간에 맞춰 다시 붙인다
            if source.is_remote:
                listing = active.remote_tree(src)
                join = lambda base, rel: shares_mod.join(base, rel)
            else:
                listing = fsops.walk_tree(src)
                join = lambda base, rel: base / rel

            for entry in listing:
                plan.append(
                    ("%s/%s" % (name, entry["path"]), join(src, entry["path"]), entry["size"])
                )
        return plan

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
        left_width = cols // 2
        left = self._pane_box(self.local, left_width, height)
        right = self._pane_box(self.remote, cols - left_width, height)
        return [left[i] + right[i] for i in range(height)]

    def _pane_box(self, pane, box_width, height):
        inner = box_width - 2
        focused = pane is self.focus
        edge = term.FG_CYAN if focused else term.FG_GRAY

        # 라벨은 온전히 두고 경로만 앞을 자른다. 뒤를 자르면 지금 폴더 이름이
        # 사라져 어디를 보고 있는지 알 수 없다
        label = pane.label
        room = inner - 2 - screen.width(label) - 2
        title = "%s  %s" % (label, screen.fit_left(pane.location, max(room, 0)))
        title = screen.fit(title, inner - 2)

        top = edge + BOX_TL + BOX_H + term.RESET
        top += (term.BOLD if focused else "") + title + term.RESET
        top += edge + BOX_H * max(inner - screen.width(title) - 1, 0) + BOX_TR + term.RESET

        body_height = height - 2
        pane.scroll_into_view(body_height)
        rows = pane.rows()

        out = [top]
        for index in range(body_height):
            position = pane.offset + index
            content = (
                self._entry_line(pane, rows[position], position, inner, focused)
                if position < len(rows)
                else " " * inner
            )
            out.append(edge + BOX_V + term.RESET + content + edge + BOX_V + term.RESET)

        footer = pane.message or self._pane_summary(pane)
        footer = screen.fit(footer, inner - 2)
        bottom = edge + BOX_BL + BOX_H + term.RESET + term.FG_GRAY + footer + term.RESET
        bottom += edge + BOX_H * max(inner - screen.width(footer) - 1, 0) + BOX_BR + term.RESET
        out.append(bottom)
        return out

    def _entry_line(self, pane, item, position, inner, focused):
        is_cursor = position == pane.cursor
        picked = item["name"] in pane.selected and not item.get("up")

        mark = "*" if picked else " "
        name = item["name"] + ("/" if item["is_dir"] and not item.get("up") else "")
        size = "" if item["is_dir"] else fsops.human_size(item["size"])

        name_width = inner - SIZE_COL - 2
        text = " %s%s%s" % (mark, screen.pad(name, name_width), screen.rjust(size, SIZE_COL))
        text = screen.pad(text, inner)

        if is_cursor and focused:
            return term.REVERSE + text + term.RESET
        if is_cursor:
            return term.BOLD + text + term.RESET
        if picked:
            return term.FG_YELLOW + text + term.RESET
        if item["is_dir"]:
            return term.FG_CYAN + text + term.RESET
        return text

    def _pane_summary(self, pane):
        if not pane.active:
            return "F2 로 연결하세요"
        if pane.selected:
            total = sum(e["size"] for e in pane.entries if e["name"] in pane.selected)
            return "%d개 선택 . %s" % (len(pane.selected), fsops.human_size(total))

        count = "%d개 항목" % len(pane.entries)
        # C:\ 에서는 '..' 이 없어 더 올라갈 곳이 안 보인다. 나갈 길을 알려준다
        if not pane.is_remote and fsops.is_filesystem_root(pane.path):
            return count + "   \\ 드라이브 선택"
        return count

    def _status(self, cols):
        return screen.pad(" " + self.status, cols)

    def _fkey_bar(self, cols):
        """MDir.Norton Commander 특유의 하단 기능키 줄.

        칸 하나가 7칸보다 좁아지면 라벨이 잘려 읽을 수 없으므로, 그때는
        짧은 안내 문구로 물러난다.
        """
        cell = cols // len(_FKEYS)
        if cell < 7:
            short = " F1도움 F2연결 F3보기 F5복사 F9정렬 F10끝내"
            return term.FG_GRAY + screen.pad(short, cols) + term.RESET

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

        줄 문자열에 ANSI 가 섞여 있어 가로 일부만 정확히 덧씌우기는 까다롭다.
        그래서 다이얼로그를 화면 폭 가까이 넓게 잡고 해당 줄을 통째로 교체한다.
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

        left_pad = " " * max((cols - box_width) // 2, 0)
        start = max((rows - len(box)) // 2, 0)
        for index, line in enumerate(box):
            row = start + index
            if 0 <= row < rows:
                while len(lines) <= row:
                    lines.append("")
                lines[row] = screen.pad(left_pad + term.FG_CYAN + line + term.RESET, cols)


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

_FKEYS = [
    ("1", "도움"),
    ("2", "연결"),
    ("3", "보기"),
    ("4", "폴더"),
    ("5", "복사"),
    ("6", "끊기"),
    ("7", "만들"),
    ("8", "삭제"),
    ("9", "정렬"),
    ("0", "끝내"),
]


def _adder(transfer):
    """진행률 콜백. 받은 바이트 수를 누적한다."""

    def add(n):
        transfer.done += n

    return add


_HELP_LINES = [
    "왼쪽은 늘 내 PC, 오른쪽은 연결된 상대입니다.",
    "",
    "Tab           패널 전환",
    "^ v k j       커서 이동    PgUp/PgDn 페이지    Home/End 처음과 끝",
    "Enter         디렉터리 열기 (.. 은 상위로)",
    "< h           상위 디렉터리",
    "Space, Insert 선택 토글",
    "+  -          마스크로 한꺼번에 선택 / 해제  (예: *.txt)",
    "\\             드라이브 / 볼륨 바꾸기 (내 PC 쪽만)",
    "",
    "기능키는 하단 바에 적힌 숫자를 그대로 눌러도 됩니다.",
    "맥에서는 F 키에 fn 이 필요하니 숫자가 편합니다.",
    "",
    "1 . F1  도움말              2 . F2  연결할 PC 찾기",
    "3 . F3  파일 보기 (v)       4 . F4  공유 폴더 바꾸기 (o)",
    "5 . F5  복사 (c)            6 . F6  연결 끊기 (d)",
    "7 . F7  폴더 만들기 (m)     8 . F8  삭제 (Delete)",
    "9 . F9  정렬 바꾸기 (s)     0 . F10 종료 (q)",
    "r             새로고침",
    "",
    "정렬은 이름 > 확장자 > 크기 > 날짜 순으로 돌아갑니다.",
    "Windows 에서 C:\\ 위로는 못 올라가므로 \\ 로 드라이브를 고릅니다.",
    "복사와 삭제는 선택한 항목이 없으면 커서에 있는 것 하나를 씁니다.",
    "상대가 연결을 요청하면 승인 창이 자동으로 뜹니다.",
]
