# WS-SSH

<p align="center">
  <img src="docs/logo.svg" alt="WS-SSH" width="560">
</p>

PTY 기반 SSH 중계 서버와 내부 에이전트입니다.

일반적인 원격 실행 도구처럼 `command → execute → response` 방식으로 동작하지 않습니다.  
SSH 세션의 입력과 출력을 바이트 스트림 그대로 전달해, 중계 서버를 거쳐 내부 서버의 실제 PTY에 연결합니다.

덕분에 `bash`, `vim`, `top`, `htop`, `nano`, `python`, `ssh` 같은 대화형 프로그램도 일반 SSH처럼 사용할 수 있습니다.

## 어떻게 동작하나요?

```text
SSH Client
    │
    │ stdin / terminal bytes
    ▼
┌──────────────────┐
│   Relay Server   │
│ SSH + WebSocket  │
└────────┬─────────┘
         │ binary frames
         ▼
┌──────────────────┐
│  Internal Agent  │
│   PTY / ConPTY   │
└────────┬─────────┘
         │
         ▼
      Shell
```

에이전트는 내부 서버에서 릴레이 서버로 먼저 연결합니다. 따라서 내부 서버에 SSH 포트를 외부로 열 필요가 없습니다.

로그인 이후에는 릴레이가 터미널 내용을 해석하지 않고 그대로 전달합니다. ANSI escape sequence, 컬러 출력, 커서 이동, UTF-8 데이터도 동일한 방식으로 통과합니다.

---

## 주요 기능

- SSH를 통한 내부 서버 접속
- WebSocket 기반 양방향 터미널 스트리밍
- Linux PTY 및 Windows ConPTY 지원
- 터미널 창 크기 실시간 동기화
- 여러 서버와 여러 세션 관리
- 관리자용 C2 CLI
- 서버별 로그인 계정 및 비밀번호 관리
- 에이전트 토큰 발급 및 재발급
- 내부 서버 설치용 원라이너 제공
- 호스트 / 네트워크 / 프로세스 정보 조회
- 긴 출력에 대한 backpressure 처리
- systemd 기반 서비스 설치
- 프로토콜 / 보안 / E2E 테스트

### 요구사항과 구현 위치

| 기능 | 구현 |
| --- | --- |
| 키보드 입력 전달 | `relay/ssh_server.py` → `relay/registry.py` |
| stdout / stderr 전달 | `agent/pty_backend.py` → `agent/agent.py` |
| C2 CLI 입력 처리 | `relay/lineeditor.py` |
| 방향키 / Home / End / Delete | `relay/lineeditor.py` |
| Ctrl+C / Ctrl+D / Ctrl+L | `relay/lineeditor.py` 및 브리지 모드 |
| 터미널 resize | `asyncssh` → `RESIZE` → `TIOCSWINSZ` |
| ANSI / 컬러 출력 | raw byte stream + `TERM` / `COLORTERM` |
| 긴 출력 처리 | 비동기 큐 + `pause_writing` / `resume_writing` |
| Linux PTY | `pty.fork()` |
| Windows PTY | pywinpty / ConPTY |
| 관리자 C2 | `relay/ssh_server.py` |
| 세션 관리 | `relay/registry.py` |
| 로그 | `relay/logs.py` |
| 서버 정보 조회 | `relay/ssh_server.py` + `agent/sysinfo.py` |

---

## 프로젝트 구조

`relay/`와 `agent/`는 서로 독립적으로 배포할 수 있습니다.

두 폴더는 서로 import하지 않고, 공통 프로토콜은 각각 `protocol.py`로 가지고 있습니다. 두 파일의 동일성은 테스트로 확인합니다.

```text
relay/
├── __main__.py          # SSH / WebSocket / 설치 웹 서버 실행
├── ssh_server.py        # SSH 서버, C2 CLI, 서버 접속
├── lineeditor.py        # C2 CLI 라인 에디터
├── ws_server.py         # 에이전트 WebSocket 수신 및 인증
├── web.py               # 에이전트 설치 웹 서버
├── registry.py          # 에이전트 연결 및 세션 관리
├── logs.py              # 최근 로그 링버퍼
├── db.py                # SQLite 및 자격증명 관리
├── security.py          # PBKDF2 해시 / 토큰
├── config.py            # YAML + 환경변수 설정
├── manage.py            # DB 초기화 및 관리 CLI
├── protocol.py          # 공통 프로토콜
├── requirements.txt
└── setup.sh

agent/
├── __main__.py
├── agent.py             # WebSocket 클라이언트 / 세션 멀티플렉싱
├── sysinfo.py           # 호스트 / 네트워크 / 프로세스 정보
├── pty_backend.py       # Unix PTY / Windows ConPTY
├── protocol.py          # 공통 프로토콜
├── requirements.txt
└── setup.sh

tests/
└── 프로토콜 / 보안 / CLI / DB / 로그 / 웹 / E2E 테스트
```

---

## 프로토콜

릴레이와 에이전트는 하나의 WebSocket 연결을 유지하면서 여러 셸 세션을 멀티플렉싱합니다.

모든 프레임은 다음과 같은 5바이트 헤더로 시작합니다.

```text
┌──────┬──────────────┬───────────────┐
│ type │ session id   │ payload       │
│ 1 B  │ u32, big-end │ variable      │
└──────┴──────────────┴───────────────┘
```

| Type | 값 | 방향 | 용도 |
| --- | --- | --- | --- |
| `AUTH` | `0x01` | agent → relay | 에이전트 인증 |
| `AUTH_OK` / `AUTH_FAIL` | `0x02` / `0x03` | relay → agent | 인증 결과 |
| `OPEN` | `0x10` | relay → agent | 셸 세션 생성 |
| `OPENED` | `0x11` | agent → relay | 세션 생성 결과 |
| `DATA` | `0x20` | 양방향 | raw 터미널 데이터 |
| `RESIZE` | `0x21` | 양방향 | 터미널 크기 변경 |
| `CLOSE` | `0x22` | 양방향 | 세션 종료 |
| `PING` / `PONG` | `0x30` / `0x31` | 양방향 | keepalive |
| `EXIT` | `0x40` | 양방향 | 연결 종료 |
| `SYSINFO` | `0x50` | relay → agent | 시스템 정보 요청 |
| `SYSINFO_RES` | `0x51` | agent → relay | 시스템 정보 응답 |

`DATA` 프레임은 문자열 명령어로 해석하지 않습니다. `vim`의 제어 시퀀스나 `top`의 화면 출력처럼 터미널에서 발생하는 데이터를 그대로 전달합니다.

---

## 설치

### 기본 설치

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Windows에서 에이전트를 실행한다면:

```powershell
pip install pywinpty
```

---

## Ubuntu에서 systemd로 설치하기

### 1. Relay Server

공인 서버 또는 중계 서버에서 실행합니다.

```bash
git clone <repo>
cd ws-ssh

sudo ./relay/setup.sh \
    --ssh-port 2222 \
    --ws-port 8765 \
    --web-port 1234 \
    --advertise-host <relay-public-ip-or-domain> \
    --admin-user alice \
    --admin-password 'strong-password'
```

설치 스크립트가 처리하는 작업:

- Python 및 필요한 패키지 설치
- `/opt/ws-ssh-relay`에 애플리케이션 설치
- `/etc/ws-ssh/relay.yaml` 생성
- `/var/lib/ws-ssh/relay.db` 초기화
- `agent/`를 설치용 `agent-dist.tar.gz`로 패키징
- `ws-relay.service` 등록 및 실행

주요 옵션:

```text
--ssh-port
--ws-port
--web-host
--web-port
--advertise-host
--admin-user
--admin-password
--service-user
--install-dir
--config-dir
--data-dir
--no-service
--skip-deps
--allow-nonroot
```

### 2. 내부 서버 추가

Relay에 SSH로 접속한 뒤 C2 CLI에서 서버를 등록합니다.

```bash
ssh alice@<relay-host> -p 2222
```

```text
C2> add-server server-001
Login password for new server server-001: ********
Server 'server-001' created.

Run this one-liner on the internal server (as root):
  curl -fsSL 'http://<relay-host>:1234/install/server-001?token=ws_...' | sudo bash
```

내부 서버에서는 출력된 한 줄만 실행하면 됩니다.

```text
agent-dist.tar.gz 다운로드
        ↓
/opt/ws-ssh-agent 설치
        ↓
/etc/ws-ssh/agent.env 저장
        ↓
ws-agent.service 등록
        ↓
에이전트 시작
```

에이전트가 Relay로 먼저 연결하기 때문에 내부 서버의 인바운드 포트를 열 필요가 없습니다.

비밀번호에 `!!`, 공백, `$`, 백틱 등이 포함되어 있다면 명령어에 직접 넣지 않는 것을 권장합니다.

```text
C2> add-server server-001
Login password for new server server-001: ********
```

이 방식은 셸의 history expansion이나 quoting 때문에 비밀번호가 변형되는 문제를 피할 수 있습니다.

수동 설치가 필요한 경우에는 `agent/setup.sh`를 직접 실행하거나 다음 명령으로 토큰과 설치 링크를 확인할 수 있습니다.

```bash
python -m relay.manage add-server ...
```

---

## Relay 주소 설정

에이전트가 접속할 Relay 주소는 다음 순서로 결정됩니다.

1. `relay.advertise_host`
2. `relay/setup.sh --advertise-host`
3. SSH 접속에 사용한 주소
4. `WS_RELAY_ADVERTISE_HOST`

예를 들어:

```yaml
relay:
  advertise_host: relay.example.com
  ws_port: 8765
  ws_path: /agent

web:
  web_port: 1234
```

NAT나 프록시 뒤에서 운영한다면 `advertise_host`를 직접 지정하는 편이 안전합니다.

포트를 변경했다면 `--ws-port`와 `--web-port`도 함께 맞춰야 합니다.

TLS를 사용할 경우에는 nginx나 Caddy 같은 reverse proxy 뒤에 두고 `wss://`, `https://`를 사용하는 구성을 권장합니다.

---

## 빠르게 실행하기

### 1. 설정과 DB 초기화

```bash
cp config.example.yaml config.yaml

python -m relay.manage -c config.yaml init
python -m relay.manage -c config.yaml add-user alice
python -m relay.manage -c config.yaml add-server server-001
```

`add-server`에서 생성된 에이전트 토큰은 한 번만 출력됩니다.

### 2. Relay 실행

```bash
python -m relay -c config.yaml
```

기본 포트:

```text
SSH : 0.0.0.0:2222
WS  : ws://0.0.0.0:8765/agent
WEB : http://0.0.0.0:1234
```

### 3. 내부 서버에 Agent 실행

Linux:

```bash
python -m agent \
  --relay ws://relay-host:8765/agent \
  --id server-001 \
  --token ws_xxxxxxxxxxxxxxxxxxxx \
  --shell /bin/bash
```

Windows:

```powershell
python -m agent `
  --relay ws://relay-host:8765/agent `
  --id win-001 `
  --token ws_... `
  --shell cmd.exe
```

셸의 시작 디렉터리는 기본적으로 에이전트를 실행한 사용자의 홈 디렉터리입니다.

변경하려면:

```bash
python -m agent --cwd /path
```

또는 `WS_CWD`, 설정 파일의 `cwd`를 사용할 수 있습니다.

우선순위는 다음과 같습니다.

```text
Relay OPEN의 cwd
    ↓
--cwd / WS_CWD / cwd
    ↓
사용자 홈 디렉터리
```

Linux에서는 기본 셸로 bash를 우선 사용합니다.

```text
/bin/bash
/usr/bin/bash
/usr/local/bin/bash
    ↓
$SHELL
    ↓
sh
```

다른 셸을 사용하려면 `--shell` 또는 `WS_SHELL`을 지정하면 됩니다.

---

