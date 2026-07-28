# MDirNet 이 쓰는 포트를 Windows Defender 방화벽에서 허용하는 스크립트 (관리자 권한 필요)
#
#   PowerShell 을 관리자로 열고:  .\tools\allow-firewall.ps1
#   되돌리려면:                  .\tools\allow-firewall.ps1 -Remove

param(
    [switch]$Remove,        # 우리가 넣은 허용 규칙을 되돌린다
    [switch]$RemoveBlocks,  # python.exe 를 막고 있는 인바운드 차단 규칙을 지운다
    [switch]$Pause          # 끝나고 창을 붙잡아 둔다 (UAC 로 새 창에서 열릴 때)
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "관리자 권한이 필요합니다. PowerShell 을 '관리자로 실행' 한 뒤 다시 시도하세요."
    exit 1
}

# 포트를 여기 적어두면 config.py 와 어긋난다. 앱에서 직접 읽어온다
$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$reader = "import sys; sys.path.insert(0, r'$here'); from mdirnet import config; " +
          "print(config.TCP_PORT); print(config.UDP_PORT)"

$python = $null
foreach ($candidate in @("py -3", "python")) {
    $parts = $candidate.Split(" ")
    if (Get-Command $parts[0] -ErrorAction SilentlyContinue) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    Write-Error "Python 을 찾을 수 없습니다. Python 3.9 이상을 설치하세요."
    exit 1
}

$parts = $python.Split(" ")
$ports = & $parts[0] $parts[1..($parts.Length - 1)] -c $reader
$tcpBase = [int]$ports[0]
$udpPort = [int]$ports[1]

# 세션 포트는 이미 쓰는 중이면 하나씩 올려가며 10번까지 시도한다 (Server.bind).
# 같은 PC 에서 두 인스턴스를 띄우는 경우까지 덮으려면 그 범위를 열어야 한다
$tcpRange = "$tcpBase-$($tcpBase + 9)"

$rules = @(
    @{ Name = "MDirNet 세션 (TCP $tcpRange)"; Protocol = "TCP"; Port = $tcpRange },
    @{ Name = "MDirNet 발견 (UDP $udpPort)";  Protocol = "UDP"; Port = "$udpPort" }
)

foreach ($rule in $rules) {
    Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    if ($Remove) {
        Write-Host "제거: $($rule.Name)"
        continue
    }

    # Public(카페, 공용 와이파이)은 일부러 뺀다. 전송이 암호화되지 않으므로
    # 집·회사 네트워크에서만 열어두는 것이 맞다
    New-NetFirewallRule -DisplayName $rule.Name `
        -Direction Inbound -Action Allow `
        -Protocol $rule.Protocol -LocalPort $rule.Port `
        -Profile Private, Domain | Out-Null
    Write-Host "허용: $($rule.Name)  (Private, Domain 프로필)"
}

# Windows 는 차단 규칙을 허용 규칙보다 먼저 본다. python.exe 를 막는 규칙이
# 남아 있으면 위에서 넣은 포트 허용은 아무 효과가 없다
if ($RemoveBlocks) {
    $blocked = Get-NetFirewallApplicationFilter |
        Where-Object { $_.Program -like "*python*" } |
        Get-NetFirewallRule |
        Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Block" }

    if (-not $blocked) {
        Write-Host "python.exe 를 막는 차단 규칙은 없습니다."
    }
    foreach ($rule in $blocked) {
        Remove-NetFirewallRule -Name $rule.Name
        Write-Host "차단 규칙 제거: $($rule.DisplayName)"
    }
}

if (-not $Remove) {
    Write-Host ""
    Write-Host "들어오는 연결을 허용했습니다. 나가는 연결은 Windows 기본값이 이미 허용입니다."
    Write-Host "네트워크가 '공용'으로 잡혀 있으면 설정 > 네트워크에서 '개인'으로 바꾸세요."
}

if ($Pause) {
    Write-Host ""
    Read-Host "Enter 를 누르면 닫힙니다"
}
