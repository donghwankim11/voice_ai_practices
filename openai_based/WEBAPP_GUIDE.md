# 배달의민족 음성 AGENT 시뮬레이터 — 사용/구조 가이드

배달의민족 고객센터의 **AGENT(AI 상담사)** 가 **RIDER(사용자, 마이크 입력)** 에게 전화를 걸어,
배달 주소 변경 건(102동 → 112동)의 정상 배달 여부를 확인하는 시나리오를 시연합니다.

두 가지 버전이 있습니다 — 목적에 맞춰 골라 쓰세요.

| 파일 | 모드 | 모델 | UI | 끼어들기(barge-in) |
|------|------|------|------|------|
| `app.py` | **턴 기반** (녹음 → 전송 → 응답 재생) | STT + LLM + TTS 조합 | Gradio | 불가 |
| `app_realtime.py` | **실시간 양방향 + 서버 VAD** | `gpt-realtime-mini` 단일 모델 | FastAPI + WebSocket + 단일 HTML | **가능** (자동 인터럽션) |

---

## 1. 빠른 시작

### 1-1. 사전 준비

- macOS / Linux + Python 3.10
- conda (miniconda 권장)
- Azure OpenAI 리소스 + 다음 deployment
  - `gpt-4o-mini-transcribe` (STT — `app.py` 전용)
  - `gpt-4o-mini-tts` (TTS — `app.py` 전용)
  - `gpt-5.4-mini` (LLM — `app.py` 전용)
  - `gpt-realtime-mini` (Realtime — `app_realtime.py` 전용)

### 1-2. `.env` 작성

같은 디렉토리(`voice_ai_practices/openai_based/`)에 `.env`:

```dotenv
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
```

### 1-3. 의존성 설치

```bash
# 1) requirements.in → requirements.txt 잠금
uv pip compile requirements.in -o requirements.txt --python-version 3.10

# 2) conda env(voice_ai_practices) 생성 + uv pip sync + ipykernel 등록
bash setup_env.sh
```

`setup_env.sh` 는 **conda env 생성 → uv 설치 → 의존성 sync → Jupyter 커널 등록** 까지 한 번에 해줍니다.

### 1-4. 웹앱 실행

```bash
conda activate voice_ai_practices

# (A) 턴 기반 버전 — 안정성 우선
python app.py
# → http://localhost:7860

# (B) 실시간 + VAD 버전 — 끼어들기 가능
python app_realtime.py
# → http://localhost:7861
```

브라우저에서 해당 포트로 접속.

> **마이크 권한**: 처음 접속 시 브라우저가 마이크 사용 권한을 묻습니다. 허용 필요.
> Safari 의 경우 `localhost` 가 아니라 IP 로 접속하면 마이크가 막힐 수 있으니 가급적 `localhost` 사용.
> 실시간 버전(`app_realtime.py`)은 `AudioWorklet` 을 사용하므로 최신 Chrome / Edge / Firefox / Safari 권장.

---

## 2. 시나리오 (요약)

`app.py` 의 `SCENARIO_SYSTEM_PROMPT`, `app_realtime.py` 의 `SCENARIO_INSTRUCTIONS` 에
시스템 프롬프트로 들어갑니다.

- **목표(검증할 사실 3가지)**
  1. 사장님으로부터 변경된 주소(112동)를 전달받았는지
  2. 실제로 112동으로 배달했는지
  3. 음식이 고객에게 정상 전달되었는지
- **톤**: 한국어 존댓말, 정중·간결, 한 응답 1~3문장.
- **출력 제약**: markdown / 글머리표 / 이모지 / 괄호 부연 금지 (그대로 음성으로 합성되기 때문).

목표 시나리오의 이상적인 대화 흐름은 `GUIDE.md` 에 명시되어 있습니다.

---

## 3. 코드 구조

```
voice_ai_practices/openai_based/
├── app.py                       # 턴 기반(Gradio) 시뮬레이터
├── app_realtime.py              # 실시간 + 서버 VAD(FastAPI + WebSocket) 시뮬레이터
├── test_openai_apis.ipynb       # 4개 모델(STT/TTS/LLM/Realtime) API 단독 테스트.
├── requirements.in              # top-level 의존성 (사용자가 == 로 핀)
├── requirements.txt             # uv pip compile 산출물
├── setup_env.sh                 # conda env + uv + ipykernel 등록 자동화
├── .env                         # Azure 자격증명 (git 제외)
├── GUIDE.md                     # 작업 목적/시나리오 정의 (입력 스펙)
└── WEBAPP_GUIDE.md              # ← 이 파일
```

### 3-A. `app.py` (턴 기반) 내부 구성

| 영역 | 위치 | 역할 |
|------|------|------|
| Azure 클라이언트/배포명 | 파일 상단 | `AzureOpenAI` 동기 클라이언트 1개 + 4개 deployment 상수 |
| 시나리오 프롬프트 | `SCENARIO_SYSTEM_PROMPT`, `OPENING_LINE` | AGENT 의 역할/목표/톤/출력 제약 정의 |
| API 헬퍼 | `synthesize_tts`, `transcribe`, `llm_reply` | 각각 TTS / STT / LLM 호출. 모두 동기. |
| 핸들러 | `start_call`, `turn`, `reset` | Gradio 이벤트에 1:1 대응. 상태(history)와 뷰(chat_view)를 함께 갱신. |
| UI | `gr.Blocks(...)` | Chatbot + AGENT 오디오(자동재생) + 마이크 입력 + 3개 버튼 |

#### 한 턴의 데이터 흐름 (`app.py`)

```
[브라우저 마이크 녹음 (.webm/.wav 임시 파일 경로)]
        │
        ▼
transcribe()                     ──▶ Azure STT (gpt-4o-mini-transcribe)
        │
        ▼
history += {role:"user", text}
        │
        ▼
llm_reply(history)               ──▶ Azure LLM (gpt-5.4-mini)
        │
        ▼
history += {role:"assistant", text}
        │
        ▼
synthesize_tts(assistant_text)   ──▶ Azure TTS (gpt-4o-mini-tts)
        │
        ▼
[MP3 파일 경로] → gr.Audio(autoplay=True) 가 자동 재생
```

### 3-B. `app_realtime.py` (실시간 + VAD) 내부 구성

단일 파일에 **FastAPI 서버 + 브라우저용 HTML/JS 페이지**가 함께 들어 있습니다.

| 영역 | 위치 | 역할 |
|------|------|------|
| Realtime 클라이언트 | 상단 `async_client = AsyncAzureOpenAI(...)` | `AsyncAzureOpenAI.beta.realtime.connect` 용 |
| 시나리오/지시문 | `SCENARIO_INSTRUCTIONS`, `OPENING_LINE` | 시스템 instructions + 첫 발화 지시 1줄 |
| FastAPI 라우트 | `@app.get("/")`, `@app.websocket("/ws")` | HTML 서빙 + 브라우저 ↔ Realtime 브릿지 |
| 브릿지 코루틴 | `from_client()`, `from_openai()` | 양방향 비동기 메시지 펌프 (`asyncio.wait`) |
| 브라우저 페이지 | `INDEX_HTML` (인라인 HTML+JS) | 마이크 캡처 + WebSocket + 큐 기반 재생 |

#### 시그널 흐름 — 정상 턴

```
[브라우저 마이크]
   │ AudioWorklet: PCM16 / 24kHz / mono / 40ms 청크
   ▼
WebSocket {type:"audio", audio:<base64>}
   │
   ▼
FastAPI /ws  ──▶  oai.input_audio_buffer.append(audio=...)
                       │
                       ▼ (서버 VAD 가 발화 시작/끝 자동 판정)
                  AGENT 응답 생성
                       │
                       ▼
   ◀── response.audio.delta              ──▶ schedulePcm16Playback() (큐 누적)
   ◀── response.audio_transcript.delta   ──▶ AGENT 말풍선 실시간 업데이트
   ◀── response.audio_transcript.done    ──▶ AGENT 말풍선 확정
```

#### 시그널 흐름 — RIDER 가 끼어들기 (barge-in)

```
[RIDER 가 AGENT 발화 도중에 말 시작]
   │
   ▼
서버 VAD: input_audio_buffer.speech_started
   │
   │ session 의 turn_detection.interrupt_response=True 이므로
   │ Realtime API 가 자동으로 직전 response 를 cancel
   │
   ▼
FastAPI 가 즉시 클라이언트에 {type:"interrupt"} 전송
   │
   ▼
브라우저: playback.reset() — 이미 큐잉된 BufferSourceNode 일괄 stop
   │
   ▼
RIDER 발화가 끝나면(VAD silence_duration_ms) 다음 AGENT 응답이 새로 흘러나옴
```

#### 세션 설정 핵심값 (`app_realtime.py`)

```python
await oai.session.update(session={
    "modalities": ["text", "audio"],
    "voice": "alloy",
    "instructions": SCENARIO_INSTRUCTIONS,
    "input_audio_format":  "pcm16",   # 24kHz mono
    "output_audio_format": "pcm16",   # 24kHz mono
    "input_audio_transcription": {    # RIDER 발화도 화면 텍스트로 표시
        "model": "gpt-4o-mini-transcribe",
    },
    "turn_detection": {
        "type": "server_vad",
        "threshold": 0.5,             # 음성 검출 민감도
        "prefix_padding_ms": 300,     # 발화 시작 직전 보존 길이
        "silence_duration_ms": 500,   # 이만큼 무음이면 발화 끝
        "create_response": True,      # 발화 끝나면 자동으로 응답 생성
        "interrupt_response": True,   # AGENT 발화 도중 끼어들기 자동 처리
    },
})
```

#### 채팅 로그 흐름 (AGENT / RIDER 양쪽 모두 표시)

| 이벤트 | 클라이언트 동작 |
|------|------|
| `response.audio_transcript.delta` | AGENT 말풍선이 실시간으로 한 글자씩 채워짐 |
| `response.audio_transcript.done` | AGENT 말풍선 확정 |
| `input_audio_buffer.speech_started` | RIDER 말풍선을 `…` 로 미리 띄움 (전사 기다리는 동안) |
| `input_audio_buffer.speech_stopped` | 말풍선 표시를 `(전사 중…)` 로 갱신 |
| `conversation.item.input_audio_transcription.delta` | RIDER 말풍선에 부분 전사 채움 (지원 시) |
| `conversation.item.input_audio_transcription.completed` | RIDER 말풍선 최종 텍스트 확정 |

#### 브라우저 측 핵심

- **AudioWorklet**: 메인 스레드를 막지 않고 PCM16 24kHz 청크를 40ms 단위로 잘라 WebSocket 으로 송신.
- **재생 큐**: `audio_delta` 가 도착할 때마다 `AudioBufferSourceNode` 를 만들어 이어붙여 스케줄. `playback.nextStart` 로 끊김 없이 연결.
- **인터럽션**: `interrupt` 메시지를 받으면 큐의 모든 `BufferSourceNode` 를 `stop()` → 진행 중이던 AGENT 발화가 즉시 끊김.

---

## 4. 두 버전의 차이 한눈에 보기

| 항목 | `app.py` | `app_realtime.py` |
|------|------|------|
| 통신 모델 | HTTP 동기 (요청/응답) | WebSocket 양방향 스트림 |
| AI 모델 | STT + LLM + TTS 조합 (3 hop) | `gpt-realtime-mini` 단일 모델 |
| 발화 단위 | 한 번에 통째로 (마이크 → 전송 버튼) | 40ms 청크 연속 송신 |
| AGENT 발화 도중 RIDER 끼어들기 | 불가 | **가능 (서버 VAD + 자동 cancel)** |
| 응답 첫소리까지 지연 | STT + LLM + TTS 합산 (~2~5초) | 모델 첫 audio_delta 까지 (~수백 ms) |
| 구현 복잡도 | 낮음 | 중간 (WebSocket + AudioWorklet + 재생 큐) |
| 안정성 | 높음 (모든 단계 동기 + 파일 경로) | 중간 (브라우저 권한/네트워크/모델 상태에 더 민감) |
| 시연 임팩트 | "응답이 자동 재생되네" | "전화처럼 끼어들어도 자연스럽게 흘러가네" |

권장: 처음에는 `app.py` 로 시나리오 검증 → `app_realtime.py` 로 실제 통화감 시연.

---

## 5. 자주 만나는 문제

### Q. (`app.py`) `Speech.create() got an unexpected keyword argument 'instructions'`
TTS `instructions` 인자는 openai SDK **1.70+** 에서 지원합니다.
`requirements.in` 의 `openai==1.78.0` 그대로 두고 `pip-compile` → `uv pip sync` 다시 수행하세요.

### Q. (`app.py`) 마이크 입력이 빈 텍스트로 인식됨
- 너무 짧거나 무음에 가까우면 STT 결과가 빈 문자열로 옵니다 → "다시 녹음해주세요" 안내가 뜹니다.
- 브라우저 마이크 권한이 차단되어 있는지 확인.

### Q. (`app_realtime.py`) AGENT 가 첫 발화를 안 함
- `gpt-realtime-mini` deployment 가 Azure 리소스에 실제로 존재하는지 확인.
- 서버 콘솔에 `[oai error]` 로그가 떴다면 그 메시지를 먼저 확인.

### Q. (`app_realtime.py`) 끼어들었는데 AGENT 가 안 끊김
- 브라우저 콘솔에서 `interrupt` 메시지가 도착하는지 확인.
- 도착했는데도 소리가 계속 나면 OS/브라우저의 오디오 버퍼링 문제 — `silence_duration_ms` 를 더 짧게(300ms 정도) 줄여보세요.

### Q. (`app_realtime.py`) 에코/하울링이 발생
- 가능하면 헤드폰 사용. `getUserMedia` 의 `echoCancellation: true` 가 켜져 있지만 스피커 음량이 크면 한계가 있음.

### Q. AGENT 가 markdown(`**`, `-`) 을 섞어서 발화하면 어색해요
시스템 프롬프트의 "출력 형식" 절에서 markdown/이모지/글머리표 금지를 강제하고 있지만,
모델이 가끔 어길 수 있습니다. 그럴 땐 프롬프트에 부정 예시를 추가해 한 번 더 강조하세요.

### Q. 외부에서 접속하고 싶어요
- `app.py`: `demo.launch(...)` 에 `share=True` 추가 → Gradio 가 임시 공개 URL 발급.
- `app_realtime.py`: ngrok / cloudflared 등으로 7861 포트 터널링. WebSocket 도 같이 통과해야 합니다.
