# 포트, 청크 크기, 타임아웃 등 프로그램 전역 상수를 모아둔 모듈

APP_NAME = "MDirNet"
PROTOCOL_VERSION = 2

# 네트워크 포트
TCP_PORT = 9600  # 세션용 TCP
UDP_PORT = 9601  # 피어 발견용 UDP 브로드캐스트

# 피어 발견 주기
ANNOUNCE_INTERVAL = 2.0  # 초. 이 주기로 자기 존재를 브로드캐스트한다
PEER_TTL = 6.0  # 초. 이 시간 동안 소식이 없으면 목록에서 지운다

# 전송
CHUNK_SIZE = 256 * 1024  # 한 프레임에 담는 파일 데이터 크기
MAX_VIEW_BYTES = 256 * 1024  # 내장 뷰어가 읽어들이는 최대 크기

# 프레임 상한. 악의적이거나 깨진 상대가 메모리를 고갈시키지 못하게 막는다
MAX_HEADER_BYTES = 1 * 1024 * 1024
MAX_BLOB_BYTES = 4 * 1024 * 1024

# 타임아웃
CONNECT_TIMEOUT = 8.0  # TCP 연결 시도
APPROVAL_TIMEOUT = 30.0  # 상대가 승인 버튼을 누를 때까지 기다리는 시간
REQUEST_TIMEOUT = 30.0  # 일반 요청-응답

# UI
UI_TICK = 0.05  # 초. 키 입력 폴링 간격
