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

