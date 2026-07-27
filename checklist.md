# MDirNet 체크리스트

## 0. 프로젝트 기반
- [x] git 저장소 초기화 (main)
- [x] PLAN.md 작성
- [x] checklist.md 작성
- [x] context-notes.md 작성
- [ ] `.gitignore` 작성
- [ ] 패키지 골격 (`mdirnet/`, `tests/`)

## 1. 프로토콜 계층
- [ ] `config.py` — 포트, 청크 크기, 타임아웃, 프레임 상한 상수
- [ ] `protocol.py` — `encode_frame()` / `FrameReader.read()`
- [ ] blob(raw 바이너리) 이어붙이기 지원
- [ ] 프레임 크기 상한 초과 시 예외
- [ ] verify: 헤더만 / 헤더+blob / 분할 수신 왕복 테스트 통과

## 2. 파일시스템 계층
- [ ] `fsops.py` — `list_dir()` 이 이름·디렉터리여부·크기·mtime 반환
- [ ] 디렉터리 우선 + 이름 오름차순 정렬
- [ ] `resolve_within()` — 공유 루트 밖 경로 거부
- [ ] `walk_tree()` — 재귀 전송용 상대 경로 목록
- [ ] 권한 없는 디렉터리를 만나도 죽지 않음
- [ ] verify: 경로 탈출(`../..`, 심링크) 차단 테스트 통과

## 3. 피어 발견
- [ ] `discovery.py` — announcer 스레드 (2초 주기 UDP 브로드캐스트)
- [ ] listener 스레드 — 피어 테이블 갱신
- [ ] 자기 자신 announce 무시 (인스턴스 ID 비교)
- [ ] 6초 무응답 피어 만료
- [ ] verify: 루프백에서 두 인스턴스가 서로를 발견

## 4. 세션 계층
- [ ] `session.py` — TCP accept 루프
- [ ] `hello` 요청 → UI 승인 이벤트 → `hello.ok` / `hello.no`
- [ ] 승인 30초 타임아웃
- [ ] 요청-응답 상관 (`id` 기반, 대칭 양방향)
- [ ] 리더 스레드에서 들어온 요청 처리 (`list`, `stat`, `tree`, `pull`)
- [ ] `bye` 및 소켓 단절 처리
- [ ] verify: 루프백 승인 후 양쪽이 서로 `list` 성공

## 5. 전송
- [ ] 파일 pull (상대 → 나)
- [ ] 파일 push (나 → 상대)
- [ ] `.part` 로 받고 완료 후 rename
- [ ] 디렉터리 재귀 전송 (`tree` 로 목록 받고 파일별 순차 전송)
- [ ] 진행률 이벤트를 UI 큐로 보고
- [ ] 덮어쓰기 확인
- [ ] verify: 1 MiB 이상 파일과 중첩 디렉터리 왕복 후 내용 해시 일치

## 6. TUI
- [ ] `term.py` — raw 모드 진입/복원 (POSIX termios, Windows msvcrt)
- [ ] 키 디코딩 — 방향키, F1~F8, Enter, Space, Ctrl-C
- [ ] Windows 10 ANSI 활성화 (`ENABLE_VIRTUAL_TERMINAL_PROCESSING`)
- [ ] `screen.py` — 더블 버퍼, 변경된 줄만 다시 그림
- [ ] 터미널 리사이즈 대응
- [ ] `app.py` — 듀얼 패널, 커서, 선택, 상태줄
- [ ] 피어 목록 다이얼로그 (F2)
- [ ] 연결 승인 다이얼로그 (수신 시 자동 표시)
- [ ] 진행률 다이얼로그
- [ ] 오류 토스트
- [ ] 종료 시 터미널 상태 복원 (예외·시그널 경로 포함)

## 7. 마무리
- [ ] `__main__.py` — `--name`, `--root`, `--port`, `--no-discovery` 인자
- [ ] README.md — 설치 없는 실행법, 키 바인딩, 방화벽 안내
- [ ] verify: `python -m unittest discover tests -v` 전체 통과
- [ ] verify: 터미널 두 개로 수동 연결·복사 확인
- [ ] 커밋 정리 (계층별 semantic commit)
