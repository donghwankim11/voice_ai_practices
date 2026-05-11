# 배달의민족 음성 AGENT 시뮬레이터 — ElevenLabs Conversational AI

배달의민족 고객센터의 **AGENT(AI 상담사)** 가 **RIDER(사용자, 마이크 입력)** 에게 전화를 걸어,
배달 주소 변경 건(102동 → 112동)의 정상 배달 여부를 확인하는 시나리오를 시연합니다.

`openai_based/app_realtime.py` (Azure OpenAI Realtime API) 의 ElevenLabs 카운터파트로,
**STT + LLM + TTS + VAD + 인터럽션이 한 WebSocket 에 통합된 Conversational AI** 를 사용합니다.

| 항목 | OpenAI 버전 (`openai_based/app_realtime.py`) | 본 버전 (ElevenLabs) |
|------|--------------------------------------------|-----|
| 단일 모델 | `gpt-realtime-mini` | ElevenLabs Convai (LLM 은 내부에서 GPT-4o-mini 등 선택) |
| 인증 | `xi-api-key` ↔ `AZURE_OPENAI_API_KEY` | `xi-api-key` 헤더로 WebSocket 연결 |
| 사전 설정 | Azure deployment 4개 | ElevenLabs **agent 1개** (헬퍼 스크립트로 생성) |
| 입력 오디오 | PCM16 24kHz | PCM16 **16kHz** |
| VAD | 서버 VAD + interrupt_response=True | 서버 측 VAD (항상 on, 인터럽션 자동) |
| 포트 | 7861 | 7862 |

---

## 1. 빠른 시작

### 1-1. 사전 준비
- macOS / Linux + Python 3.10
- conda (miniconda 권장)
- ElevenLabs 계정 + API key (https://elevenlabs.io/app/settings/api-keys)
  - Conversational AI 사용 가능한 플랜이어야 함 (free 도 가능, 무료 분량 한정)

### 1-2. `.env` 작성
같은 디렉토리(`voice_ai_practices/elevenlabs_based/`)에 `.env`:

```dotenv
ELEVENLABS_API_KEY=sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ELEVENLABS_AGENT_ID=                     # create_agent.py 가 자동 채워줌
# ELEVENLABS_VOICE_ID=                   # (선택) 한국어 voice 오버라이드
```

`.env.example` 을 복사해 사용해도 됩니다.

### 1-3. 의존성 설치

```bash
# 1) requirements.in → requirements.txt 잠금
uv pip compile requirements.in -o requirements.txt --python-version 3.10

# 2) conda env (voice_ai_elevenlabs) 생성 + 동기화 + ipykernel 등록
bash setup_env.sh
```

### 1-4. Agent 생성 (1회)

```bash
conda activate voice_ai_elevenlabs
python create_agent.py
```

- 시나리오 프롬프트(`SCENARIO_PROMPT`) + 첫 발화(`OPENING_LINE`) + 한국어 + 다국어 voice 로
  ElevenLabs 에 신규 agent 가 등록됩니다.
- 출력된 `agent_id` 가 `.env` 의 `ELEVENLABS_AGENT_ID` 에 자동 저장됩니다.
- 이미 `.env` 에 값이 있으면 덮어쓰지 않고 안내만 합니다 (다시 만들려면 해당 라인 삭제 후 재실행).

> 대시보드(https://elevenlabs.io/app/conversational-ai)에서 수동으로 agent 를 만들고
> agent_id 만 직접 `.env` 에 넣어도 됩니다. 단, 시나리오 프롬프트/언어/voice 설정을
> 동일하게 맞춰야 합니다.

### 1-5. 웹앱 실행

```bash
python app_realtime.py
# → http://localhost:7862
```

브라우저에서 접속 → 마이크 권한 허용 → **📞 통화 시작**.

> **마이크 권한**: Safari 의 경우 `localhost` 가 아니라 IP 로 접속하면 마이크가 막힐 수 있으니
> 가급적 `localhost` 사용. AudioWorklet 을 사용하므로 최신 Chrome / Edge / Firefox / Safari 권장.

> **헤드폰 사용 권장** — 스피커로 들으면 AGENT 음성이 마이크로 들어가 자가 인식 루프가
> 발생할 수 있습니다 (`echoCancellation: true` 가 켜져 있지만 스피커 음량이 크면 한계).

---

## 2. 시나리오

`app_realtime.py` 의 `SCENARIO_PROMPT` / `OPENING_LINE` 에 정의 (`create_agent.py` 가
agent 생성 시 그대로 사용 + 매 세션 시작 시 override 로 재적용).

- **목표(검증할 사실 3가지)**
  1. 사장님으로부터 변경된 주소(112동)를 전달받았는지
  2. 실제로 112동으로 배달했는지
  3. 음식이 고객에게 정상 전달되었는지
- **톤**: 한국어 존댓말, 정중·간결, 한 응답 1~3문장.
- **출력 제약**: markdown / 글머리표 / 이모지 / 괄호 부연 금지.

이상적인 대화 흐름은 `GUIDE.md` 참고.

---

## 3. 코드 구조

```
voice_ai_practices/elevenlabs_based/
├── app_realtime.py        # FastAPI + WebSocket 브릿지 + 인라인 HTML/JS
├── create_agent.py        # ElevenLabs agent 생성 헬퍼 (1회 실행)
├── requirements.in        # top-level 의존성
├── requirements.txt       # uv pip compile 산출물 (사용자가 생성)
├── setup_env.sh           # conda env (voice_ai_elevenlabs) + uv + ipykernel
├── .env                   # ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID (git 제외)
├── .env.example           # .env 템플릿
├── GUIDE.md               # 작업 목적/시나리오 정의
└── WEBAPP_GUIDE.md        # ← 이 파일
```

### 3-A. `app_realtime.py` 내부 구성

| 영역 | 위치 | 역할 |
|------|------|------|
| 환경 변수 | 상단 `load_dotenv()` 블록 | `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID` 검증 |
| WebSocket URL | `WS_URL` | `wss://api.elevenlabs.io/v1/convai/conversation?agent_id=...&output_format=pcm_16000` |
| 시나리오 | `SCENARIO_PROMPT`, `OPENING_LINE` | 매 세션 시작 시 override 로 재주입 |
| FastAPI 라우트 | `@app.get("/")`, `@app.websocket("/ws")` | HTML 서빙 + 브라우저 ↔ Convai 브릿지 |
| 브릿지 코루틴 | `from_client()`, `from_eleven()` | 양방향 비동기 메시지 펌프 |
| 브라우저 페이지 | `INDEX_HTML` | 마이크 캡처 + WebSocket + 큐 기반 PCM 재생 |

### 3-B. 시그널 흐름 — 정상 턴

```
[브라우저 마이크]
   │ AudioWorklet: PCM16 / 16kHz / mono / 40ms 청크 (640 샘플)
   ▼
WebSocket {type:"audio", audio:<base64>}
   │
   ▼
FastAPI /ws  ──▶  ElevenLabs {"user_audio_chunk":"<base64>"}
                       │
                       ▼ (Convai 서버 VAD 가 발화 시작/끝 자동 판정)
                  AGENT 응답 생성 (LLM + TTS 통합)
                       │
                       ▼
   ◀── {"type":"audio", audio_event:{audio_base_64}}      ──▶ schedulePcm16Playback() (큐 누적)
   ◀── {"type":"agent_response", agent_response_event}     ──▶ AGENT 말풍선 출력
   ◀── {"type":"user_transcript", user_transcription_event} ──▶ RIDER 말풍선 출력
```

### 3-C. 시그널 흐름 — RIDER 가 끼어들기 (barge-in)

```
[RIDER 가 AGENT 발화 도중에 말 시작]
   │
   ▼
ElevenLabs Convai 가 자동으로 진행 중인 응답을 cancel
   │
   ▼
서버에 {"type":"interruption"} 이벤트 전송
   │
   ▼
FastAPI 가 즉시 클라이언트에 {type:"interrupt"} 전송
   │
   ▼
브라우저: playback.reset() — 큐잉된 BufferSourceNode 일괄 stop
   │
   ▼
RIDER 발화가 끝나면 다음 AGENT 응답이 새로 흘러나옴
```

### 3-D. 핵심 ElevenLabs 메시지 타입

**클라이언트 → 서버:**
| 타입 | 의미 |
|------|------|
| `conversation_initiation_client_data` | 세션 시작 시 1회. `conversation_config_override` 로 prompt/first_message/language/voice 등을 override 가능 |
| `{"user_audio_chunk":"<b64>"}` | 마이크 PCM16 16kHz 청크 |
| `{"type":"pong","event_id":N}` | 서버 ping 에 대한 응답 (keep-alive) |

**서버 → 클라이언트:**
| 타입 | 의미 |
|------|------|
| `conversation_initiation_metadata` | 세션 메타데이터 (1회) |
| `audio` | TTS 출력 PCM16 base64 (`audio_event.audio_base_64`) |
| `agent_response` | AGENT 의 최종 텍스트 |
| `agent_response_correction` | 인터럽션 후 잘려나간 부분을 반영한 corrected text |
| `user_transcript` | RIDER STT 결과 |
| `interruption` | AGENT 발화가 끊겼음 → 클라이언트 재생 큐 비우기 |
| `ping` | keep-alive (반드시 `pong` 회신) |

---

## 4. OpenAI 버전과의 차이

| 항목 | `openai_based/app_realtime.py` | 본 버전 |
|------|-----|-----|
| 인증 | Azure OpenAI 키 + endpoint + deployment 4개 | ElevenLabs 키 1개 + agent_id 1개 |
| 시나리오 주입 | `session.update(instructions=...)` | agent 생성 시 + `conversation_initiation_client_data` override |
| 입력 audio | PCM16 24kHz, 40ms 청크 | PCM16 **16kHz**, 40ms 청크 (640 샘플) |
| 출력 audio | `response.audio.delta` (24kHz PCM) | `audio` 이벤트 (16kHz PCM, URL 쿼리로 강제) |
| AGENT 텍스트 | `response.audio_transcript.delta/done` (스트리밍 가능) | `agent_response` (최종만, 부분 delta 없음) |
| RIDER 전사 | `conversation.item.input_audio_transcription.*` | `user_transcript` (최종만) |
| barge-in 트리거 | `input_audio_buffer.speech_started` | `interruption` 이벤트 |
| ping/pong | 자동 (SDK 처리) | 수동 (`ping` → `pong` 회신 필수) |

---

## 5. 자주 만나는 문제

### Q. `ELEVENLABS_AGENT_ID 가 필요합니다` 에러
`python create_agent.py` 를 먼저 실행. 또는 대시보드에서 만들고 `.env` 에 직접 입력.

### Q. WebSocket 이 1006 / 401 로 즉시 끊김
- API key 가 유효한지 확인.
- 무료 플랜의 Convai 사용량을 다 썼을 가능성. 대시보드의 사용량 확인.
- agent_id 가 본인 계정 소유인지 확인.

### Q. AGENT 가 첫 발화를 안 함
- agent 의 `first_message` 와 `language` 설정이 비어있을 가능성.
- 본 데모는 매 세션 `conversation_initiation_client_data.conversation_config_override.agent.first_message`
  로 override 하지만, agent 의 platform_settings 에서 override 권한이 꺼져 있으면 무시됨.
  `create_agent.py` 가 자동으로 권한을 켜둠 — 대시보드에서 끈 적이 있다면 확인.

### Q. AGENT 가 한국어 대신 영어로 답함
- agent `language` 가 `en` 으로 돼 있을 수 있음. `create_agent.py` 는 `ko` 로 생성.
- voice 가 한국어 발음이 어색할 수 있음 — `.env` 의 `ELEVENLABS_VOICE_ID` 로 다른 voice
  지정 후 `python create_agent.py` 재실행 (이전 agent 삭제 또는 ELEVENLABS_AGENT_ID 비우기).

### Q. 음성이 잘려서 들리거나 끊김
- 네트워크 지연. 브라우저 콘솔에서 audio_delta 도착 간격 확인.
- AudioWorklet 의 `nextStart` 큐가 너무 빡빡하면 짧은 chunk 가 누락될 수 있음 — 코드의
  `Math.max(audioCtx.currentTime + 0.02, playback.nextStart)` 의 0.02 (=20ms 여유)
  를 0.05 정도로 늘려서 테스트.

### Q. 끼어들었는데 AGENT 가 안 끊김
- `interruption` 이벤트가 도착하는지 서버 콘솔에서 확인.
- ElevenLabs 의 VAD 민감도는 agent 설정 (`turn_detection`) 에서 조정 가능. 대시보드 또는
  agent 업데이트 API 로 변경.

### Q. 외부에서 접속하고 싶어요
- ngrok / cloudflared 등으로 7862 포트 터널링. WebSocket(`/ws`) 도 같이 통과해야 합니다.

---

## 6. 왜 OpenAI Realtime API 처럼 자연스럽게 주고받지 못하나?

ElevenLabs Convai 가 단일 WebSocket 으로 묶여 있어도, OpenAI Realtime 만큼의
응답성을 *구조적으로* 따라잡을 수 없습니다. 인터페이스가 같다고 응답 지연 특성까지
같지는 않습니다.

### 6-1. 근본 원인 — 모델 아키텍처가 다르다

**OpenAI Realtime (`gpt-realtime` / `gpt-realtime-mini`)** — speech-to-speech 단일 모델
- 음성 → 음성을 *하나의 모델*이 직접 처리 (텍스트 중간 단계 없음)
- 모델이 "듣고 생각하면서 동시에 말하기 시작" 가능 (진정한 스트리밍)
- 첫 음성 출력까지 ~300–500ms

**ElevenLabs Convai** — STT → LLM → TTS *파이프라인*
- 단일 WebSocket 으로 묶여 있을 뿐, 내부는 3단계 직렬 처리
- RIDER 발화 종료 → STT 확정 → LLM 응답 생성 → TTS 합성 → 청크 송신
- 단계마다 지연이 누적, 첫 청크까지 ~800ms–1.5s

> 본 가이드 상단의 *"STT + LLM + TTS + VAD + 인터럽션이 한 WebSocket 에 통합"* 은
> *인터페이스* 관점에서만 사실이고, 응답 지연 특성은 OpenAI Realtime 과 본질적으로 다릅니다.

### 6-2. 코드/설정에서 더 줄일 수 있는 지점

| 지점 | 위치 | 권장 |
|------|------|------|
| `turn_timeout` | `app_realtime.py:112` (현재 1.0s) | 0.4–0.5s 로 낮추면 RIDER 발화 끝나고 응답까지의 침묵이 짧아짐 (단, 짧은 호흡에 잘리는 인터럽션 ↑) |
| TTS `speed` | `app_realtime.py:117` (현재 1.15) | 더 올리면 자연스러움 손상 — 1.15 가 거의 한계 |
| LLM 모델 | agent 설정 (`create_agent.py`) | 가장 빠른 변종으로: `gpt-4o-mini`, `gemini-flash`, `llama-3.1-8b` 등 |
| TTS 모델 | agent 설정 (`create_agent.py`) | `eleven_flash_v2_5` (~75ms) 가 `eleven_multilingual_v2` (~400ms+) 보다 빠름 |

### 6-3. 결론

"OpenAI Realtime 처럼 안 되는" 건 코드 버그가 아니라 ElevenLabs 의 *구조적 한계*입니다.
STT + LLM + TTS 파이프라인은 single-model speech-to-speech 의 응답성을 따라잡을 수 없습니다.
비슷한 자연스러움이 필요하다면:

- 위 표의 모든 단계를 가장 빠른 변종으로 교체하고
- `turn_timeout` 을 0.4–0.5s 로 낮추는 것이 현실적인 한계.

진짜 동급의 응답성이 필요하다면 `openai_based/app_realtime.py` (Azure OpenAI Realtime API)
를 사용하세요.

---

## 7. ElevenLabs 에 OpenAI Realtime 같은 speech-to-speech 단일 모델 API 가 있나?

**없습니다.** ElevenLabs 에는 "추론하면서 동시에 말하는" 단일 멀티모달 음성 모델이
존재하지 않습니다.

### 7-1. ElevenLabs 의 "Speech-to-Speech" 는 다른 개념

ElevenLabs 도 `Speech-to-Speech` 라는 이름의 API 를 제공하지만, 이것은
**음성 변환(voice conversion)** 입니다:

- 입력: 사람이 말한 오디오
- 출력: *같은 내용* 을 *다른 보이스/톤* 으로 바꾼 오디오
- 목적: 더빙, 보이스 클로닝, 캐릭터 변환

즉 "내용은 그대로 두고 목소리만 바꾸는" 기능이지, **이해하고 응답하는** 대화형 모델이
아닙니다. OpenAI Realtime 의 speech-to-speech 와는 의미가 완전히 다릅니다.

### 7-2. 왜 차이가 나는가 — 회사별 핵심 역량

| 회사 | 핵심 역량 | 대화형 음성 접근 |
|------|----------|------------------|
| **OpenAI** | 대규모 멀티모달 LLM (GPT-4o) | 음성을 직접 토큰화해 LLM 이 처리하는 native multimodal |
| **Google** | 멀티모달 LLM (Gemini) | Gemini Live API — OpenAI Realtime 과 유사한 native S2S |
| **ElevenLabs** | TTS / 보이스 합성 전문 | 자체 LLM 없음 → 외부 LLM 을 STT/TTS 로 감싸는 Convai 파이프라인 |

ElevenLabs 는 본질적으로 **TTS 스페셜리스트** 이지 LLM 회사가 아닙니다.
Convai 도 내부적으로 OpenAI/Anthropic/Google/Llama 등 외부 LLM 을 골라 쓰는 구조이며
(`create_agent.py` 에서 LLM 선택 가능), 단일 멀티모달 음성 모델을 직접 만들 동기와
학습 자원이 다릅니다.

### 7-3. 비슷한 응답성을 원한다면 선택지

1. **Azure OpenAI Realtime** — 이미 `openai_based/app_realtime.py` 에서 사용 중
2. **OpenAI Realtime API (직접)** — Azure 거치지 않는 버전
3. **Google Gemini Live API** — Native S2S, 한국어 지원
4. **Kyutai Moshi (오픈소스)** — Native S2S 오픈 모델, 자가 호스팅

ElevenLabs 의 강점은 *보이스 품질* 이므로, "응답성" 이 핵심이면 다른 스택을 쓰고,
"한국어 보이스가 자연스러워야 한다" 가 핵심이면 Convai 를 쓰면서 6-2 표의 단축안을
적용하는 게 트레이드오프입니다.
