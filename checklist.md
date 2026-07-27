# MDirNet 체크리스트

## 0. 프로젝트 기반
- [x] git 저장소 초기화 (main)
- [x] PLAN.md 작성
- [x] checklist.md 작성
- [x] context-notes.md 작성
- [x] `.gitignore` 작성
- [x] 패키지 골격 (`mdirnet/`, `tests/`)

## 1. 프로토콜 계층
- [x] `config.py` — 포트, 청크 크기, 타임아웃, 프레임 상한 상수
- [x] `protocol.py` — `encode_frame()` / `FrameReader.read()`
- [x] blob(raw 바이너리) 이어붙이기 지원
- [x] 프레임 크기 상한 초과 시 예외
- [x] verify: 헤더만 / 헤더+blob / 분할 수신 왕복 테스트 통과

## 2. 파일시스템 계층
- [x] `fsops.py` — `list_dir()` 이 이름·디렉터리여부·크기·mtime 반환
- [x] 디렉터리 우선 + 이름 오름차순 정렬
- [x] `resolve_within()` — 공유 루트 밖 경로 거부
- [x] `walk_tree()` — 재귀 전송용 상대 경로 목록
- [x] 권한 없는 디렉터리를 만나도 죽지 않음
- [x] verify: 경로 탈출(`../..`, 심링크) 차단 테스트 통과

## 3. 피어 발견
- [x] `discovery.py` — announcer 스레드 (2초 주기 UDP 브로드캐스트)
- [x] listener 스레드 — 피어 테이블 갱신
- [x] 자기 자신 announce 무시 (인스턴스 ID 비교)
- [x] 6초 무응답 피어 만료
- [x] verify: 루프백에서 두 인스턴스가 서로를 발견

## 4. 세션 계층
- [x] `session.py` — TCP accept 루프
- [x] `hello` 요청 → UI 승인 이벤트 → `hello.ok` / `hello.no`
- [x] 승인 30초 타임아웃
- [x] 요청-응답 상관 (`id` 기반, 대칭 양방향)
- [x] 리더 스레드에서 들어온 요청 처리 (`list`, `stat`, `tree`, `pull`)
- [x] `bye` 및 소켓 단절 처리
- [x] verify: 루프백 승인 후 양쪽이 서로 `list` 성공

## 5. 전송
- [x] 파일 pull (상대 → 나)
- [x] 파일 push (나 → 상대)
- [x] `.part` 로 받고 완료 후 rename
- [x] 디렉터리 재귀 전송 (`tree` 로 목록 받고 파일별 순차 전송)
- [x] 진행률 이벤트를 UI 큐로 보고
- [x] 덮어쓰기 확인
- [x] verify: 1 MiB 이상 파일과 중첩 디렉터리 왕복 후 내용 해시 일치

## 6. TUI
- [x] `term.py` — raw 모드 진입/복원 (POSIX termios 확인, Windows msvcrt 는 코드만)
- [x] 키 디코딩 — 방향키, F1~F8, Enter, Space, Ctrl-C
- [~] Windows 10 ANSI 활성화 (`ENABLE_VIRTUAL_TERMINAL_PROCESSING`) — 코드만, 실기 미확인
- [x] `screen.py` — 더블 버퍼, 변경된 줄만 다시 그림
- [x] 터미널 리사이즈 대응
- [x] `app.py` — 듀얼 패널, 커서, 선택, 상태줄
- [x] 피어 목록 다이얼로그 (F2)
- [x] 연결 승인 다이얼로그 (수신 시 자동 표시)
- [x] 진행률 다이얼로그
- [x] 오류 토스트
- [x] 종료 시 터미널 상태 복원 (예외·시그널 경로 포함)

## 7. 마무리
- [x] `__main__.py` — `--name`, `--root`, `--port`, `--no-discovery` 인자
- [x] README.md — 설치 없는 실행법, 키 바인딩, 방화벽 안내
- [x] verify: `python -m unittest discover tests -v` 전체 통과
- [x] verify: 터미널 두 개로 수동 연결·복사 확인
- [x] 커밋 정리 (계층별 semantic commit)

## 8. 공유 폴더 선택상자 (2026-07-28 추가)
- [x] `FolderDialog` — 하위 폴더만 나열, Enter 진입, ← 상위, Ctrl-D 결정, Esc 취소
- [x] 폴더를 옮겨도 상자 높이가 변하지 않게 빈 줄로 자리 유지
- [x] `--root` 생략 시 시작 시점에 자동 표시
- [x] 폴더 확정 전에는 서버·브로드캐스트를 시작하지 않음
- [x] 시작 시점 Esc 는 종료 (아무것도 공유하지 않은 상태)
- [x] F4 로 재개, 연결 중에는 거부
- [x] verify: 실제 터미널에서 표시 → 진입 → Ctrl-D 확정 → F4 재개 → Esc 취소 확인

## 9. MDir 관습 재현 (2026-07-28 추가)
- [x] 하단 F1~F10 라벨 바 (칸이 7칸보다 좁으면 짧은 안내로 물러남)
- [x] 마스크 선택 `+` / 해제 `-` (`*.txt`, 대소문자 무시)
- [x] Insert 키로 선택 토글
- [x] 내장 파일 보기 F3 — 텍스트 / 바이너리 16진수, 원격은 앞부분만 받음
- [x] 폴더 만들기 F7 (로컬·원격)
- [x] 삭제 F8 / Delete (로컬·원격, 재귀, 되돌릴 수 없음 경고)
- [x] 정렬 전환 F9 — 이름 → 확장자 → 크기 → 날짜, 폴더는 늘 위
- [x] 프로토콜에 `head` / `mkdir` / `delete` 추가
- [x] 원격 삭제가 공유 루트 자체를 지우지 못하게 차단
- [x] 심링크는 링크만 지우고 대상은 남김
- [x] 왼쪽 패널 라벨을 '내 PC' 로 명확화
- [x] 문서에서 "MDir 계승" 과장 정정 — 키 배치는 NC 관습, MDir 에는 네트워크가 없었음
- [x] verify: 실제 터미널에서 기능키 바·마스크 선택·텍스트/16진수 뷰어 확인

## 10. 남은 것
- [ ] Windows 실기 확인 (msvcrt 키 입력, ANSI 활성화, 경로 표시)
- [ ] 전송 중 취소 (Esc) — 현재는 끝날 때까지 기다려야 한다
- [ ] 덮어쓰기를 항목별로 묻기 — 현재는 일괄 확인 한 번

## 검증 기록 (2026-07-27)
- `python3 -m unittest discover -s tests -t .` → 180개 통과
- pty 스모크 — 첫 화면·F1 모달·커서 이동·선택 렌더링 확인, `q` 종료 코드 0, 터미널 복원 확인
- 두 프로세스 실기 — 상호 발견 → F2 요청 → 승인 → 한글 중첩 디렉터리(2MB 바이너리 포함)
  재귀 복사 → 해시 3/3 일치, `.part` 잔여물 없음, 양쪽 트레이스백 없음

- [ ] 압축 파일 안을 디렉터리처럼 탐색 (MDir 에는 있던 기능)
- [ ] 디렉터리 트리 패널 (MDir 에는 있던 기능)
- [ ] 이름 바꾸기 / 이동
