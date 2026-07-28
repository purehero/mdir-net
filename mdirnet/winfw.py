# Windows 방화벽 규칙을 읽어보고, 필요하면 관리자 권한으로 열어달라고 요청하는 모듈
#
# 규칙 변경은 Windows 가 관리자 전용으로 못박아 두었다. 조용히 여는 길은 없다.
# 그래서 읽기(권한 필요 없음)와 쓰기(UAC 동의 창)를 나눈다.

import json
import os
import subprocess

RULE_PREFIX = "MDirNet"  # tools/allow-firewall.ps1 이 붙이는 이름
NEEDED_RULES = 2  # 세션(TCP)과 발견(UDP)
TIMEOUT = 20.0

# 읽기만 한다. 표준 사용자도 규칙 목록은 볼 수 있다
_INSPECT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$allow = @(Get-NetFirewallRule -DisplayName '%s*' |
    Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' })
$blocks = @(Get-NetFirewallApplicationFilter |
    Where-Object { $_.Program -like '*python*' } |
    Get-NetFirewallRule |
    Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' } |
    Select-Object -ExpandProperty DisplayName)
[pscustomobject]@{ allow = $allow.Count; blocks = @($blocks) } | ConvertTo-Json -Compress
"""


class Unknown(Exception):
    """규칙 상태를 알아낼 수 없다. 이때는 아무것도 묻지 않는다.

    PowerShell 이 없거나 응답이 이상한 경우다. 모르는 상태로 사용자를 붙잡고
    묻는 것이 조용히 넘어가는 것보다 나쁘다.
    """


class State:
    def __init__(self, allow_count, blocks):
        self.allow_count = allow_count
        self.blocks = list(blocks)

    @property
    def allow_missing(self):
        return self.allow_count < NEEDED_RULES

    @property
    def ok(self):
        """열려 있는가. 차단 규칙이 하나라도 있으면 열려 있지 않다.

        Windows 는 **차단 규칙을 허용 규칙보다 먼저** 본다. 그래서 포트 허용을
        넣어도 python.exe 차단 규칙이 남아 있으면 아무 효과가 없다.
        """
        return not self.allow_missing and not self.blocks


def available():
    """이 OS 에서 방화벽을 다룰 수 있는가."""
    return os.name == "nt"


def _run(command):
    """PowerShell 을 돌려 표준 출력을 돌려준다. 테스트는 이 자리를 갈아끼운다."""
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unknown(str(exc))
    if done.returncode != 0:
        raise Unknown(done.stderr.strip() or "PowerShell 이 %d 로 끝났습니다" % done.returncode)
    return done.stdout


def inspect(runner=_run):
    """지금 규칙 상태. 알 수 없으면 `Unknown`."""
    raw = runner(["powershell", "-NoProfile", "-Command", _INSPECT % RULE_PREFIX])
    try:
        msg = json.loads(raw.strip() or "null")
    except json.JSONDecodeError as exc:
        raise Unknown(str(exc))
    if not isinstance(msg, dict):
        raise Unknown("응답을 해석할 수 없습니다: %.60s" % raw)

    blocks = msg.get("blocks") or []
    if isinstance(blocks, str):
        blocks = [blocks]  # ConvertTo-Json 은 항목이 하나면 배열을 벗긴다
    try:
        allow_count = int(msg.get("allow") or 0)
    except (TypeError, ValueError):
        raise Unknown("allow 값이 숫자가 아닙니다: %r" % msg.get("allow"))

    return State(allow_count, [str(b) for b in blocks])


def script_path():
    """함께 배포되는 허용 스크립트의 경로."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "allow-firewall.ps1")


def _ps_quote(text):
    """PowerShell 작은따옴표 문자열로 감싼다. 안의 따옴표는 두 번 적어 벗긴다."""
    return "'%s'" % str(text).replace("'", "''")


def allow_command(script=None, remove_blocks=False, pause=True):
    """UAC 동의 창을 띄워 허용 스크립트를 관리자로 실행하는 명령.

    `Start-Process -Verb RunAs` 가 그 동의 창이다. 동의하지 않으면 아무 일도
    일어나지 않고 우리 쪽은 그대로 계속 돈다.
    """
    script = script or script_path()
    # `Start-Process -ArgumentList` 는 항목을 공백으로 이어붙이기만 한다. 경로에
    # 공백이 있으면(`C:\내 문서\...`) 거기서 쪼개지므로 큰따옴표를 씌워 보낸다
    args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", '"%s"' % script]
    if remove_blocks:
        args.append("-RemoveBlocks")
    if pause:
        args.append("-Pause")  # 창이 곧 닫히면 오류를 볼 수 없다

    inner = "Start-Process -FilePath powershell -Verb RunAs -ArgumentList @(%s)" % ", ".join(
        _ps_quote(a) for a in args
    )
    return ["powershell", "-NoProfile", "-Command", inner]


def request(script=None, remove_blocks=False, runner=None):
    """관리자 권한으로 규칙을 넣어달라고 요청한다. 요청을 보냈으면 True."""
    command = allow_command(script, remove_blocks=remove_blocks)
    try:
        (runner or _run)(command)
    except Unknown:
        return False
    return True
