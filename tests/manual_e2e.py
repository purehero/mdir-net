# 별개 프로세스 두 개를 실제 네트워크로 붙여 전 과정을 확인하는 수동 검증 스크립트
#
#   python3 tests/manual_e2e.py
#
# unittest 가 아니라 직접 실행한다. 실제 pty 와 실제 소켓을 쓰므로 느리다.
# 진짜 두 대의 PC 로 하는 검증을 대신하지는 못한다 (같은 호스트다).
import os, pty, re, select, subprocess, sys, time, tempfile, threading, unicodedata, hashlib, pathlib, fcntl, termios, struct
REPO = str(pathlib.Path(__file__).resolve().parent.parent); UDP = "9831"
ROWS, COLS = 28, 104

def cw(ch):
    if unicodedata.combining(ch): return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W","F","A") else 1

class Screen:
    def __init__(self): self.g=[[" "]*COLS for _ in range(ROWS)]; self.r=self.c=0
    def feed(self,data):
        t=data.decode("utf-8","replace"); i=0
        while i<len(t):
            ch=t[i]
            if ch=="\x1b":
                m=re.match(r"\x1b\[([0-9;?]*)([a-zA-Z])", t[i:])
                if not m: i+=1; continue
                a,cmd=m.group(1),m.group(2)
                if cmd=="H":
                    n=[int(x) for x in a.split(";") if x.isdigit()] or [1]
                    self.r=n[0]-1; self.c=(n[1]-1) if len(n)>1 else 0
                elif cmd=="J": self.g=[[" "]*COLS for _ in range(ROWS)]
                elif cmd=="K" and 0<=self.r<ROWS:
                    for x in range(self.c,COLS): self.g[self.r][x]=" "
                i+=m.end(); continue
            if ch=="\n": self.r+=1; self.c=0; i+=1; continue
            if ch=="\r": self.c=0; i+=1; continue
            w=cw(ch)
            if 0<=self.r<ROWS and 0<=self.c<COLS:
                self.g[self.r][self.c]=ch
                for k in range(1,w):
                    if self.c+k<COLS: self.g[self.r][self.c+k]=""
            self.c+=w; i+=1
    def show(self): return "\n".join("".join(r).rstrip() for r in self.g).rstrip()

class Node:
    def __init__(self, name, shares):
        args = []
        for sh in shares: args += ["--share", str(sh)]
        self.name = name
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mdirnet", "--name", name, "--port", "0", "--udp-port", UDP] + args,
            stdin=slave, stdout=slave, stderr=slave, cwd=REPO, start_new_session=True)
        os.close(slave)
        self.new, self.all = [], []
        self._stop = threading.Event(); self.sc = Screen()
        threading.Thread(target=self._rd, daemon=True).start()
    def _rd(self):
        while not self._stop.is_set():
            if select.select([self.master],[],[],0.1)[0]:
                try: d = os.read(self.master, 65536)
                except OSError: return
                if not d: return
                self.new.append(d); self.all.append(d)
    @property
    def text(self): return b"".join(self.all).decode("utf-8","replace")
    def key(self, b, w=0.5): os.write(self.master, b); time.sleep(w)
    def type(self, text, w=0.6):
        """문자열을 한 번에 써넣는다. 한글은 여러 바이트라 쪼개면 디코딩이 끊긴다."""
        os.write(self.master, text.encode("utf-8")); time.sleep(w)
    def wait(self, needle, secs=20):
        end = time.time()+secs
        while time.time() < end:
            if needle in self.text: return True
            time.sleep(0.15)
        return False
    def screen(self, wait=0.9):
        time.sleep(wait); self.sc.feed(b"".join(self.new)); self.new.clear()
        return self.sc.show()
    def close(self):
        for _ in range(3): self.key(b"\x1b", 0.3)   # 열린 창을 닫는다
        self.key(b"0", 0.8)
        try:
            code = self.proc.wait(timeout=8)        # 읽기 스레드는 계속 돌린다
        except subprocess.TimeoutExpired:
            self.proc.kill(); code = "타임아웃"
        self._stop.set()
        return code

def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

# ---- 자료 준비 ----
base = pathlib.Path(tempfile.mkdtemp())
a_share = base / "alpha자료"; a_share.mkdir()
proj = a_share / "보낼프로젝트"; (proj/"src").mkdir(parents=True)
(proj/"README.md").write_bytes(b"# alpha\n")
(proj/"src"/"main.py").write_bytes(b"print('hello')\n"*800)
(proj/"빈폴더").mkdir()
(proj/"한글 폴더").mkdir(); (proj/"한글 폴더"/"메모.txt").write_bytes("한글 내용".encode())

b1 = base / "beta문서"; b1.mkdir()
(b1/"보고서.TXT").write_bytes(b"beta report "*900)
(b1/"MERGEDIC.EXE").write_bytes(bytes(range(256))*60)
(b1/"바꿀이름.txt").write_bytes(b"rename me")
(b1/"지울파일.txt").write_bytes(b"delete me")
b2 = base / "beta사진"; b2.mkdir(); (b2/"photo.jpg").write_bytes(b"J"*4096)

results = []
def check(label, ok, extra=""):
    results.append((label, ok)); print("  [%s] %s %s" % ("OK" if ok else "실패", label, extra))

a = Node("alpha", [a_share])
b = Node("beta", [b1, b2])
try:
    print("\n### 1. 서로 발견")
    check("alpha 가 beta 를 발견", a.wait("발견 1대", 15))
    check("beta 가 alpha 를 발견", b.wait("발견 1대", 15))

    print("\n### 2. 연결 요청과 승인")
    a.key(b"n", 1.2)
    check("피어 목록 열림", a.wait("연결할 PC 선택", 8))
    check("목록에 beta 있음", a.wait("beta", 8))
    a.key(b"\r", 1.0)
    check("beta 에 승인 창", b.wait("연결 요청", 15))
    b.key(b"y", 3.0)
    check("양쪽 연결 표시", a.wait("연결됨", 15) and b.wait("연결됨", 15))
    print("\n--- alpha 화면 (상대의 가상 루트) ---")
    print(a.screen(1.5))

    print("\n### 3. 가상 루트에 공유 2개")
    check("beta문서 보임", "beta문서" in a.text)
    check("beta사진 보임", "beta사진" in a.text)

    print("\n### 4. 공유 폴더로 진입해 파일 받기 (pull)")
    a.key(b"\t", 0.6)                    # 오른쪽 패널
    # 가상 루트에는 '..' 이 없다. 커서 0 이 곧 첫 공유(beta문서)다
    a.key(b"\r", 2.0)
    print("--- alpha 화면 (beta문서 안) ---")
    print(a.screen(1.2))
    check("beta문서 안으로 진입", "/beta문서" in a.screen(0.6))
    check("MDir 칸 표시(EXE)", "MERGEDIC" in a.text and "EXE" in a.text)
    # 보고서.TXT 를 골라 복사
    a.key(b"+", 1.0)
    for _ in range(6): a.key(b"\x7f", 0.06)   # 미리 채워진 '*.' 를 지운다
    a.type("*.TXT", 0.8)
    a.key(b"\r", 1.5)
    # *.TXT 는 대소문자를 가리지 않으므로 .txt 파일 3개가 모두 잡힌다
    check("마스크로 3개 선택 (대소문자 무시)", "3개 선택" in a.screen(0.7))
    a.key(b"5", 1.5)
    check("복사 확인 창", a.wait("덮어씁니다", 8))
    a.key(b"y", 3.5)
    got = a_share/"보고서.TXT"
    check("pull 로 받은 파일 해시 일치", got.is_file() and sha(got)==sha(b1/"보고서.TXT"))
    check("마스크로 고른 3개 모두 받음",
          all((a_share/n).is_file() for n in ("보고서.TXT", "바꿀이름.txt", "지울파일.txt")))

    print("\n### 5. 디렉터리 밀어넣기 (push, 빈 폴더 포함)")
    a.key(b"\t", 0.6)                    # 왼쪽(내 PC)
    # 마스크로 폴더를 정확히 고른다 (커서 위치 추측보다 확실하다)
    a.key(b"+", 1.0)
    for _ in range(6): a.key(b"\x7f", 0.06)   # 미리 채워진 '*.' 를 지운다
    a.type("보낼*", 0.8)
    a.key(b"\r", 1.5)
    check("push 대상이 beta문서", "/beta문서" in a.screen(0.5))
    a.key(b"5", 1.2)
    if a.wait("덮어씁니다", 6): a.key(b"y", 6.0)
    dest = b1/"보낼프로젝트"
    same = dest.is_dir() and (dest/"src"/"main.py").is_file() and \
           sha(dest/"src"/"main.py")==sha(proj/"src"/"main.py")
    check("push 로 보낸 디렉터리 재현", same)
    check("빈 폴더도 생성", (dest/"빈폴더").is_dir() if dest.is_dir() else False)
    check("한글 폴더 내용 일치",
          (dest/"한글 폴더"/"메모.txt").is_file() and
          sha(dest/"한글 폴더"/"메모.txt")==sha(proj/"한글 폴더"/"메모.txt") if dest.is_dir() else False)

    print("\n### 6. 원격 이름 바꾸기 / 삭제")
    a.key(b"\t", 0.6)                    # 오른쪽(상대)
    a.key(b"r", 2.0)                     # 목록 새로고침
    # 공유 안이므로 0 은 '..' 이다. 이름순에서 ASCII 가 한글보다 앞이므로
    # 0='..' 1=MERGEDIC.EXE 2=바꿀이름.txt ... 두 칸 내려간다
    a.key(b"\x1b[B", 0.4); a.key(b"\x1b[B", 0.6)
    a.key(b"6", 1.6)
    if a.wait("이름 바꾸기", 6):
        for _ in range(24): a.key(b"\x7f", 0.04)
        a.type("새이름.txt")
        a.key(b"\r", 2.5)
    check("원격 이름 바꾸기", (b1/"새이름.txt").is_file() or (b1/"바꿀이름.txt").is_file(),
          "(새이름=%s)" % (b1/"새이름.txt").is_file())

    print("\n### 7. 상단 메뉴의 Commands 힌트")
    a.key(b"9", 1.0); a.key(b"\x1b[C", 0.6); a.key(b"\x1b[C", 0.6)
    menu = a.screen(0.9)
    print(menu.split("\n")[0]); 
    for line in menu.split("\n"):
        if "연결" in line and ("n" in line or "d" in line): print("   ", line.strip()[:80])
    check("연결 힌트가 n", "연결할 PC 찾기" in a.text and " n " in a.text)
    a.key(b"\x1b", 0.6)

    print("\n### 8. 끊기와 종료")
    a.key(b"d", 2.0)
    check("alpha 끊김 표시", a.wait("연결을 끊었습니다", 8) or a.wait("연결 없음", 8))
    check("beta 도 끊김 감지", b.wait("연결이 끊", 8) or b.wait("종료했습니다", 8))
finally:
    print("\n### 9. 정리")
    tb_a, tb_b = "Traceback" in a.text, "Traceback" in b.text
    check("alpha 트레이스백 없음", not tb_a)
    check("beta 트레이스백 없음", not tb_b)
    ca, cb = a.close(), b.close()
    check("alpha 종료 코드 0", ca == 0, "(%r)" % ca)
    check("beta 종료 코드 0", cb == 0, "(%r)" % cb)

bad = [l for l,ok in results if not ok]
print("\n" + "="*60)
print("통과 %d / %d" % (len(results)-len(bad), len(results)))
if bad:
    print("실패 항목:")
    for l in bad: print("  -", l)
print("=> 전체:", "성공" if not bad else "일부 실패")
