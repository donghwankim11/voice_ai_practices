# Skills 기반 음성 AGENT 시뮬레이터 — 사용/구조 가이드

배달의민족 고객센터 **AGENT(AI 상담사)** 가 **RIDER(사용자, 마이크 입력)** 에게
전화를 거는 음성 시뮬레이터. 시나리오를 **Skills 패턴**으로 분리해, 시나리오를
드롭다운에서 골라 시연할 수 있다 (LangChain Skills 가이드: https://wikidocs.net/318950).

원본 `../openai_based/` 와의 차이는 1줄 요약하면:
**“시스템 프롬프트에 시나리오 본문 전체를 박지 않고, 시나리오 description 목록만 노출 → AGENT 가 통화 시작 직전에 `load_skill` 함수콜로 본문을 받아온다.”**

---

## 1. 빠른 시작

### 1-1. 사전 준비
- macOS / Linux + Python 3.10
- conda (miniconda 권장)
- Azure OpenAI 리소스 + 다음 deployment
  - `gpt-realtime-mini` (Realtime — `app_realtime.py` 메인)
  - `gpt-4o-mini-transcribe` (Realtime 입력 음성 전사 — RIDER 발화 텍스트 표시용)

### 1-2. `.env` 작성
```dotenv
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
```

### 1-3. 의존성 설치
```bash
# 1) requirements.in → requirements.txt 잠금
uv pip compile requirements.in -o requirements.txt --python-version 3.10

# 2) conda env(voice_ai_practices) 생성 + uv pip sync
bash setup_env.sh
```

### 1-4. 웹앱 실행
```bash
conda activate voice_ai_practices
python app_realtime.py
# → http://localhost:7861
```

브라우저에서 시나리오를 선택하고 **📞 통화 시작** → AGENT 가 첫 발화를 시작한다.

> **마이크 권한**: 처음 접속 시 브라우저가 마이크 사용 권한을 묻는다. 허용 필요.
> 가능하면 **헤드폰 사용** — 스피커 출력이 마이크로 들어가면 자가 인식 루프 발생.

---

## 2. 디렉토리 구조

```
voice_ai_practices/openai_based_LCSK/
├── app_realtime.py           # FastAPI + Realtime + Skills 함수콜 처리
├── skills_registry.py        # Skill 데이터클래스, SKILLS dict, load_skill 함수
├── skills/
│   ├── address_change_verification.md
│   ├── payment_missing_inquiry.md
│   ├── delivery_delay_compensation.md
│   └── coupon_refund.md
├── requirements.in           # 의존성 (langchain 미사용)
├── requirements.txt          # uv pip compile 산출물 (gitignore)
├── setup_env.sh              # conda env + uv 셋업
├── .env                      # Azure 자격증명 (gitignore)
├── .gitignore
├── GUIDE.md                  # 작업 목적 / 패턴 적용 의도
├── WEBAPP_GUIDE.md           # ← 이 파일
└── langchain_skills.md       # wikidocs 318950 요약
```

---

## 3. Skills 패턴이 코드에 들어간 자리

### 3-A. `skills_registry.py` — 패턴의 핵심

```python
@dataclass
class Skill:
    name: str
    description: str   # 시스템 프롬프트에 노출 (경량)
    content: str       # load_skill 호출 시 반환 (상세)

_SKILL_DESCRIPTIONS = {
    "address_change_verification": "배달 주소 변경 확인 — ...",
    "payment_missing_inquiry":     "결제 누락 문의 — ...",
    ...
}

SKILLS = {name: _load(name, desc) for name, desc in _SKILL_DESCRIPTIONS.items()}

def load_skill(skill_name: str) -> str:
    if skill_name not in SKILLS:
        return f"알 수 없는 스킬: {skill_name}. 사용 가능: {', '.join(SKILLS.keys())}"
    return SKILLS[skill_name].content
```

- **description** 은 dict 에서 직접 관리 (한 줄)
- **content** 는 `skills/<name>.md` 에서 로드 (도메인 담당자가 코드 수정 없이 편집 가능)

### 3-B. `app_realtime.py` — Realtime 세션에 패턴 결합

#### (1) 시스템 프롬프트는 *description 목록만*
```python
BASE_INSTRUCTIONS = f"""\
당신은 ... 상담사입니다.
...
[사용 가능한 스킬]
{skill_list_for_prompt()}        # description 1줄 × 스킬 수

[작동 규칙]
1. 시스템이 알려준 시나리오 이름으로 load_skill 을 *반드시 먼저* 호출
2. 본문의 [첫 발화] 그대로 통화 시작
"""
```

#### (2) load_skill 을 Realtime function tool 로 등록
```python
LOAD_SKILL_TOOL = {
    "type": "function",
    "name": "load_skill",
    "description": "...",
    "parameters": {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "enum": list(SKILLS.keys()), ...},
        },
        "required": ["skill_name"],
    },
}
await oai.session.update(session={
    ...
    "tools": [LOAD_SKILL_TOOL],
    "tool_choice": "auto",
})
```

#### (3) 시나리오 지정 시스템 메시지 + 첫 응답 트리거
```python
await oai.conversation.item.create(item={
    "type": "message",
    "role": "system",
    "content": [{
        "type": "input_text",
        "text": f"이번 통화에서 처리할 시나리오는 '{scenario}' 입니다. "
                "load_skill 도구로 해당 스킬 본문을 먼저 로드한 뒤, "
                "본문에 적힌 [첫 발화] 그대로 통화를 시작하세요.",
    }],
})
await oai.response.create()
```

#### (4) 함수콜 도착 시 본문 회신
```python
elif etype == "response.function_call_arguments.done":
    args = json.loads(event.arguments or "{}")
    if event.name == "load_skill":
        output = load_skill(args.get("skill_name", ""))
    else:
        output = f"알 수 없는 함수: {event.name}"
    await oai.conversation.item.create(item={
        "type": "function_call_output",
        "call_id": event.call_id,
        "output": output,
    })
    await oai.response.create()
```

이게 LangChain `@tool def load_skill(...)` 의 Realtime 등가물이다.

---

## 4. WebSocket 메시지 프로토콜

### 클라이언트 → 서버
| type | 페이로드 | 비고 |
|---|---|---|
| `start` | `{scenario: <name>}` | **첫 메시지 필수**. 받기 전엔 Realtime 세션을 열지 않음 |
| `audio` | `{audio: <base64 PCM16>}` | 마이크 청크 (40ms @ 24kHz) |
| `stop` | — | 종료 신호 |

### 서버 → 클라이언트 (원본 + Skills 관련 추가)
| type | 페이로드 | 비고 |
|---|---|---|
| `scenario_set` | `{scenario}` | 시스템 메시지 주입 완료 (load_skill 호출 직전) |
| `skill_loaded` | `{skill}` | load_skill 함수콜 처리 완료 |
| `audio_delta` | `{audio: <base64>}` | AGENT TTS PCM16 청크 |
| `agent_text_delta` / `agent_text_done` | AGENT 음성 전사 | 원본과 동일 |
| `interrupt` | — | RIDER 가 끼어들었으니 재생 큐 비우기 |
| `user_text_delta` / `user_text_done` | RIDER 입력 전사 | 원본과 동일 |
| `user_speech_stopped` | — | 발화 끝, 전사 진행 중 |
| `response_done` | — | 한 응답 종료 |
| `error` | `{message}` | 오류 |

`scenario_set`/`skill_loaded` 두 종류는 화면 상단 채팅 로그에 SYSTEM 말풍선으로 표시되어 *언제 어떤 스킬이 로드됐는지* 시연 중에 시각적으로 확인할 수 있다.

---

## 5. 전체 흐름 — 정상 통화 + barge-in

```
[브라우저]                    [FastAPI /ws]                 [Azure Realtime]
   │  start{scenario}           │                                │
   │ ────────────────────────▶ │                                │
   │                            │ session.update(tools=[load_skill])
   │                            │ ─────────────────────────────▶ │
   │                            │ conversation.item.create(system: "이번 시나리오: ...")
   │                            │ ─────────────────────────────▶ │
   │                            │ response.create                │
   │                            │ ─────────────────────────────▶ │
   │                            │                                │
   │                            │ ◀── response.function_call_arguments.done
   │                            │      (load_skill, skill_name=<name>)
   │                            │                                │
   │ ◀── skill_loaded           │                                │
   │                            │ conversation.item.create(function_call_output: <content>)
   │                            │ ─────────────────────────────▶ │
   │                            │ response.create                │
   │                            │ ─────────────────────────────▶ │
   │                            │                                │
   │ ◀── audio_delta + agent_text_delta (스트림)                  │
   │ ◀── agent_text_done                                         │
   │                            │                                │
   │  audio (마이크 청크)        │                                │
   │ ────────────────────────▶ │ input_audio_buffer.append      │
   │                            │ ─────────────────────────────▶ │
   │                            │                                │
   │ ◀── interrupt (서버 VAD speech_started 가 떨어진 즉시)        │
   │   (= 재생 큐 reset)         │                                │
```

---

## 6. 새 시나리오 추가 가이드

1. `skills/<new_skill>.md` 생성 — 다음 5개 절을 채운다.
   - `## 통화 목적`
   - `## 검증해야 할 사실`
   - `## 마무리 조건`
   - `## 첫 발화` (정확히 모델이 말할 문장)
   - `## 톤 주의` (선택)
2. `skills_registry.py` 의 `_SKILL_DESCRIPTIONS` 에 1줄 추가
   ```python
   "<new_skill>": "<한 줄 설명>",
   ```
3. 끝. 재기동하면 드롭다운에 자동 등장.

`BASE_INSTRUCTIONS` 는 description 목록만 포함하므로 시나리오 N개가 되어도
시스템 프롬프트 토큰량은 *N × 약 30 토큰* 만 증가한다.

---

## 7. 자주 만나는 문제

### Q. AGENT 가 첫 발화를 안 한다
- 서버 콘솔에서 `[skill] load_skill('...') → ... chars` 로그가 떴는지 확인.
- 안 떴다면 모델이 함수콜 대신 바로 발화를 했을 수 있음 → 시스템 메시지의 "*반드시 먼저* 호출" 표현을 더 강하게(예: "먼저 호출하지 않으면 응답을 거부하세요") 다듬어 보라.
- 떴는데도 음성이 없다면 `gpt-realtime-mini` 배포가 Azure 리소스에 실제로 있는지 확인.

### Q. 본문에 명시한 [첫 발화] 와 다른 문장으로 시작한다
- 시스템 메시지 마지막 줄을 더 단정적으로: "본문 [첫 발화] 의 따옴표 내부 문장을 글자 그대로 사용하세요."
- 본문 자체에서 [첫 발화] 절을 맨 위로 옮겨도 효과가 큼.

### Q. 스킬을 두 번 로드한다
- 보통 모델이 시나리오를 잊은 듯한 상황에서 발생. 본문을 다시 가져오는 건 자연스러운 동작이라 굳이 막을 필요 없음.
- 막고 싶다면 wikidocs 가이드 §6 (제약 조건/상태 추적) 참고: 로드 여부를 상태로 들고, 두 번째 호출 시 짧은 안내만 반환.

### Q. `conversation.item.create` 의 system role 이 거부된다
- 일부 Azure Realtime 배포 버전에서 system 역할이 제한된 사례가 있음.
- 그 경우 role 을 `"user"` 로 바꾸고 텍스트 맨 앞에 `[SYSTEM]` 같은 메타 토큰을 붙이는 워크어라운드.

### Q. 외부에서 접속하고 싶다
- ngrok / cloudflared 등으로 7861 포트 터널링. WebSocket 도 같이 통과해야 함.

---

## 8. 원본(`../openai_based`) 과 비교

| 항목 | 원본 `app_realtime.py` | 본 패키지 |
|---|---|---|
| 시나리오 수 | 1 (주소 변경) | N (드롭다운 선택) |
| 시스템 프롬프트 | 시나리오 본문 전체를 박음 | description 목록만 박음 |
| 첫 발화 트리거 | `response.create` 즉시 → 첫 발화 | system msg → load_skill 함수콜 → 첫 발화 |
| 시나리오 추가 비용 | 코드 수정 (상수 교체 또는 분기) | `.md` 1개 + dict 1줄 |
| 시스템 프롬프트 토큰 | 시나리오마다 누적 (선형 증가 위험) | description 줄 수만큼만 증가 |
| 첫 발화까지 latency | Realtime 첫 응답 | + load_skill 1라운드트립 (수백 ms) |
| barge-in / 서버 VAD | 동일 | 동일 |

권장: 시나리오 1~2개로 시연만 할 때는 원본이 더 단순하고 빠름. 3개 이상으로 늘릴 가능성이 있다면 본 패키지가 코드 수정 빈도 측면에서 유리.
