# 듀얼 패널 상태 기계, 키 바인딩, 다이얼로그를 담은 UI 본체

import os
import queue
import threading
from pathlib import Path

from .. import config, discovery, fsops, session
from . import screen, term

BOX_H, BOX_V = "─", "│"
BOX_TL, BOX_TR, BOX_BL, BOX_BR = "┌", "┐", "└", "┘"

SIZE_COL = 10  # 크기를 표시할 우측 열 폭


class Transfer:
    """진행 중인 복사 작업. 워커가 갱신하고 UI 가 매 프레임 읽어간다.

    청크마다 이벤트를 큐에 넣으면 큐가 넘치므로, 공용 객체를 두고 UI 가
    폴링하는 쪽을 택했다. 정수·문자열 대입은 GIL 아래에서 찢어지지 않는다.
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
        self.path = Path.home()
        self.entries = []
        self.cursor = 0
        self.offset = 0
        self.selected = set()
        self.message = ""
        self._generation = 0  # 늦게 도착한 조회 결과를 버리기 위한 세대 번호

    @property
    def title(self):
        if not self.is_remote:
            return "로컬  %s" % self.path
        if not self.app.session:
            return "원격  (연결되지 않음)"
        return "%s  %s" % (self.app.session.peer_name, self.path)

    @property
    def active(self):
        return not self.is_remote or self.app.session is not None

    def rows(self):
        """'..' 항목을 앞에 붙인 표시용 목록."""
        if not self.active:
            return []
        items = []
        if self.path != self.path.parent:
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
        self.path = Path(path)
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
                self.entries = fsops.list_dir(self.path)
                self.message = ""
            except OSError as exc:
                self.entries = []
                self.message = "읽을 수 없습니다: %s" % exc
            self._clamp()
            return

        self.message = "불러오는 중…"
        active = self.app.session

        def work():
            try:
                path, entries = active.remote_list(str(self.path))
                self.app.events.put(("listing", side, generation, path, entries))
            except session.SessionError as exc:
                self.app.events.put(("list_error", side, generation, str(exc)))

        threading.Thread(target=work, name="list-%s" % side, daemon=True).start()

    def apply_listing(self, generation, path, entries):
        if generation != self._generation:
            return  # 그 사이 다른 디렉터리로 옮겨갔다. 낡은 결과는 버린다
        self.path = Path(path)
        self.entries = entries
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

    def lines(self, inner_width):
        return []

    def key(self, key):
        """True 를 돌려주면 다이얼로그를 닫는다."""
        return False


class MessageDialog(Dialog):
    """오류·안내 표시. 아무 키나 누르면 닫힌다."""

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
    """예/아니오 확인. 결정되면 on_decide(bool) 을 부른다."""

    def __init__(self, title, body, on_decide):
        self.title = title
        self._body = body if isinstance(body, list) else [body]
        self._on_decide = on_decide

    def lines(self, inner_width):
        return self._body + ["", term.BOLD + "진행(Y)      취소(N)" + term.RESET]

    def key(self, key):
        if key in ("y", "Y"):
            self._on_decide(True)
            return True
        if key in ("n", "N", term.ESC, "q"):
            self._on_decide(False)
            return True
        return False


class ApprovalDialog(Dialog):
    """상대가 보낸 연결 요청. 사용자가 직접 수락/거부를 고른다."""

    title = "연결 요청"

    def __init__(self, request):
        self.request = request

    def lines(self, inner_width):
        return [
            term.BOLD + self.request.name + term.RESET,
            term.FG_GRAY + "%s · %s" % (self.request.host, self.request.platform) + term.RESET,
            "",
            "이 PC 의 공유 폴더를 열어줍니다.",
            "",
            term.BOLD + "수락(Y)      거부(N)" + term.RESET,
        ]

    def key(self, key):
        if key in ("y", "Y", term.ENTER):
            self.request.decide(True)
            return True
        if key in ("n", "N", term.ESC, "q"):
            self.request.decide(False)
            return True
        return False


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
                term.FG_YELLOW + "상대가 승인할 때까지 기다리는 중…" + term.RESET,
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
        bar = "█" * filled + "░" * (bar_width - filled)
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

    def __init__(self, my_name, root, tcp_port, udp_port, platform_name, use_discovery=True):
        self.my_name = my_name
        self.root = Path(root).expanduser().resolve()
        self.platform_name = platform_name
        self.events = queue.Queue()

        self.local = Pane(self, is_remote=False)
        self.remote = Pane(self, is_remote=True)
        self.local.path = self.root
        self.focus = self.local

        self.session = None
        self.modal = None
        self.status = "F2 를 눌러 연결할 PC 를 찾습니다"
        self.transfer = None
        self.running = True

        self.server = session.Server(my_name, platform_name, self.root, self.events, tcp_port)
        self.port = self.server.bind()
        self.discovery = (
            discovery.Discovery(os.urandom(8).hex(), my_name, self.port, platform_name, udp_port)
            if use_discovery
            else None
        )
        self.renderer = screen.Renderer()

    # --- 실행 ---

    def run(self):
        self.server.start()
        if self.discovery:
            self.discovery.start()
        self.local.refresh()

        with term.Terminal() as terminal:
            while self.running:
                self._drain_events()
                self.renderer.render(self._compose(term.size()), term.size())

                key = terminal.read_key(config.UI_TICK)
                if key is not None:
                    self._on_key(key)

        self.shutdown()

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
                home = new_session.remote_home()
                self.remote.path = Path(home)
                self.remote.refresh()
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
                    peer, self.my_name, self.platform_name, self.root, self.events
                )
                self.events.put(("connected", new_session))
            except session.SessionError as exc:
                self.events.put(("connect_failed", str(exc)))
            except OSError as exc:
                self.events.put(("connect_failed", "연결할 수 없습니다: %s" % exc))

        threading.Thread(target=work, name="connect", daemon=True).start()

    # --- 키 처리 ---

    def _on_key(self, key):
        if self.modal is not None:
            if self.modal.key(key):
                self.modal = None
            return

        if key in ("q", "CTRL-C"):
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
        elif key == term.SPACE:
            self.focus.toggle_select()
        elif key == term.ENTER:
            self._enter()
        elif key in (term.LEFT, "h"):
            self._go_up(self.focus)
        elif key in ("F5", "c"):
            self._start_copy()
        elif key in ("F2", "n"):
            self.modal = PeerDialog(self)
        elif key in ("F3", "d"):
            self._disconnect()
        elif key in ("F6", "r"):
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
            self.focus.navigate(self.focus.path / item["name"])

    def _go_up(self, pane):
        # 원격이 공유 루트 밖으로 나가려 하면 상대가 거부하고, 그 사유가
        # list_error 로 돌아와 패널 아래에 표시된다. 미리 막지 않는다
        if pane.path != pane.path.parent:
            pane.navigate(pane.path.parent)

    def _disconnect(self):
        if not self.session:
            self.status = "연결된 상대가 없습니다"
            return
        self.session.close("연결을 끊었습니다")

    # --- 복사 ---

    def _start_copy(self):
        if self.transfer:
            self.status = "이미 전송 중입니다"
            return

        source, dest = self.focus, (self.remote if self.focus is self.local else self.local)
        if not self.session:
            self.status = "먼저 F2 로 상대에 연결하세요"
            return

        items = source.targets()
        if not items:
            self.status = "복사할 항목을 고르세요 (Space 로 선택)"
            return

        names = ", ".join(i["name"] for i in items[:3]) + (" 외" if len(items) > 3 else "")
        self.modal = ConfirmDialog(
            "복사",
            [
                "%s → %s" % (source.title.split("  ")[0], dest.title.split("  ")[0]),
                "",
                screen.fit(names, 50),
                "",
                term.FG_GRAY + "대상: %s" % dest.path + term.RESET,
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
                    dest_path = dest.path / rel
                    try:
                        if source.is_remote:
                            active.pull_file(
                                str(src_path), dest_path, on_progress=_adder(transfer)
                            )
                        else:
                            active.push_file(
                                src_path, str(dest_path), on_progress=_adder(transfer)
                            )
                    except (session.SessionError, OSError) as exc:
                        errors.append("%s — %s" % (rel, exc))
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
            src = source.path / name

            if not item["is_dir"]:
                plan.append((name, src, item["size"]))
                continue

            listing = (
                active.remote_tree(str(src)) if source.is_remote else fsops.walk_tree(src)
            )
            for entry in listing:
                plan.append(
                    ("%s/%s" % (name, entry["path"]), src / entry["path"], entry["size"])
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
        lines.append(self._hints(cols))

        if self.modal is not None:
            self._overlay(lines, cols, rows)
        return lines

    def _header(self, cols):
        left = " %s  %s" % (term.BOLD + "MDirNet" + term.RESET, self.my_name)

        # 좁은 터미널에서도 연결 상태는 보여야 한다. 긴 형태부터 시도해
        # 들어가는 것을 고른다
        if self.session:
            color, variants = term.FG_GREEN, [
                "● %s 연결됨" % self.session.peer_name,
                "● %s" % self.session.peer_name,
                "●",
            ]
        else:
            found = len(self.discovery.peers()) if self.discovery else 0
            color, variants = term.FG_GRAY, [
                "○ 연결 없음 · 발견 %d대 · 포트 %d" % (found, self.port),
                "○ 발견 %d대" % found,
                "○",
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

        title = screen.fit(pane.title, inner - 2)
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
            return "%d개 선택 · %s" % (len(pane.selected), fsops.human_size(total))
        return "%d개 항목" % len(pane.entries)

    def _status(self, cols):
        return screen.pad(" " + self.status, cols)

    def _hints(self, cols):
        hints = "Tab 패널  ↑↓ 이동  Enter 열기  Space 선택  F5 복사  F2 연결  F3 끊기  F1 도움말  q 종료"
        return term.FG_GRAY + screen.pad(" " + hints, cols) + term.RESET

    def _overlay(self, lines, cols, rows):
        """모달을 화면 가운데 띄운다.

        줄 문자열에 ANSI 가 섞여 있어 가로 일부만 정확히 덧씌우기는 까다롭다.
        그래서 다이얼로그를 화면 폭 가까이 넓게 잡고 해당 줄을 통째로 교체한다.
        """
        box_width = min(max(cols - 8, 30), 72)
        inner = box_width - 4
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


def _adder(transfer):
    """진행률 콜백. 받은 바이트 수를 누적한다."""

    def add(n):
        transfer.done += n

    return add


_HELP_LINES = [
    "Tab        패널 전환",
    "↑ ↓ k j    커서 이동   PgUp/PgDn 페이지",
    "Enter      디렉터리 열기 (.. 은 상위로)",
    "← h        상위 디렉터리",
    "Space      선택 토글 (여러 개 고를 수 있습니다)",
    "F5 / c     반대 패널로 복사 (디렉터리는 재귀)",
    "F2 / n     연결할 PC 찾기",
    "F3 / d     연결 끊기",
    "F6 / r     새로고침",
    "q          종료",
    "",
    "상대가 연결을 요청하면 승인 창이 자동으로 뜹니다.",
]
