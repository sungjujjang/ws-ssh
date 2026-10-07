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

