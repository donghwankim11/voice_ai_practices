# 작업 목적
파이썬 기반으로 **여러 시나리오를 다룰 수 있는** 실시간 음성 AGENT 를 LangChain Skills 패턴을 적용해 웹앱으로 시연한다.

원본(`../openai_based`) 은 시나리오가 1개(주소 변경 확인)였고 시스템 프롬프트가 단일 상수였지만,
시연·운영에서 시나리오가 여러 개로 늘어날 가능성이 있어 **Skills 패턴(설명-본문 이중 구조 + 점진적 공개)** 으로 재구성한다.

# 베이스 코드
- `../openai_based/app_realtime.py` (Realtime API + 서버 VAD + barge-in)
- 본 디렉토리는 **app_realtime.py 만** 유지 (app.py 턴 기반 버전은 작성하지 않음)

# 적용한 패턴 — LangChain Skills (출처: https://wikidocs.net/318950)

원문은 `langchain.agents.create_agent` + `AgentMiddleware` 를 쓰지만, 그 런타임은
Realtime API 의 양방향 스트림/서버 VAD/barge-in 과 매끄럽게 결합되지 않는다.
따라서 본 패키지는 Skills 의 *패턴* 만 가져와 plain Python 으로 구현한다.

| 원문 (LangChain) | 본 패키지 (Realtime) |
|---|---|
| `Skill` 데이터클래스 (name/description/content) | 동일 — `skills_registry.py:Skill` |
| `@tool def load_skill(...)` | Realtime function tool `LOAD_SKILL_TOOL` (`app_realtime.py`) |
| `AgentMiddleware.wrap_model_call` 로 시스템 프롬프트에 description 주입 | `BASE_INSTRUCTIONS` 안에 `skill_list_for_prompt()` 호출로 정적 주입 |
| `create_agent(...)` 로 묶기 | Realtime 세션의 `session.update` 한 번에 묶기 |
| `load_skill` 호출 시 본문이 ToolMessage 로 컨텍스트에 들어감 | `response.function_call_arguments.done` → `function_call_output` → `response.create` 로 동일 효과 |

# 시나리오 — Skill 단위로 분리

본문은 `skills/<name>.md` 에 두고, description 은 `skills_registry.py` 에서 1줄로 관리.

- `address_change_verification` — 배달 주소 변경 확인 (102동 → 112동)
- `payment_missing_inquiry` — 결제 누락 문의
- `delivery_delay_compensation` — 배달 지연 보상 안내
- `coupon_refund` — 쿠폰 환불·재정산

새 시나리오를 추가하려면:
1. `skills/<new_name>.md` 작성 (통화 목적 / 검증해야 할 사실 / 첫 발화 / 톤 주의)
2. `skills_registry.py` 의 `_SKILL_DESCRIPTIONS` 에 `<new_name>: "<1줄 설명>"` 추가

기본 시스템 프롬프트(`BASE_INSTRUCTIONS`) 는 손대지 않아도 되며, **시나리오 수가 늘어도 시스템 프롬프트 토큰량은 description 줄 수 만큼만 증가**한다 (점진적 공개의 핵심 효과).

# 통화 흐름 (Skills 가 들어간 자리)

```
[브라우저] 시나리오 선택 → [통화 시작]
         │
         ▼
[WS open] 첫 메시지로 {"type":"start", "scenario": <name>} 전송
         │
         ▼
[FastAPI] Realtime 세션 open
         │  • instructions = BASE (스킬 description 목록만 포함)
         │  • tools = [load_skill]
         │  • turn_detection.server_vad + interrupt_response=True
         │
         ▼
[FastAPI] conversation.item.create(role=system, "이번 시나리오: <name>. load_skill 먼저 호출")
         │
         ▼
[FastAPI] response.create
         │
         ▼
[AGENT]   load_skill(skill_name=<name>) 함수콜
         │
         ▼
[FastAPI] response.function_call_arguments.done 수신
         │  → SKILLS[<name>].content 를 function_call_output 으로 회신
         │  → response.create
         │
         ▼
[AGENT]   본문의 [첫 발화] 그대로 통화 시작
         │
         ▼
이후는 원본과 동일 (서버 VAD / barge-in / 부분 전사 표시)
```

# 시연 시나리오 — `address_change_verification` 의 이상적인 흐름
```
AGENT: 안녕하세요, 배달의민족 고객센터입니다. 주문 건 배달 주소 확인차 연락드렸습니다. 잠시 통화 가능하실까요?
RIDER: 네, 가능합니다.
AGENT: 사장님께 변경된 주소(112동)를 전달받으셨는지 확인 가능하실까요?
RIDER: 네, 112동으로 전달받았습니다.
AGENT: 그럼 102동이 아니라 변경된 112동으로 배달 진행하신 게 맞으실까요?
RIDER: 네, 112동으로 갔습니다.
AGENT: 음식은 고객님께 정상적으로 전달 완료되었을까요?
RIDER: 네, 직접 전달 완료했습니다.
AGENT: 종합 확인 — 102동이 아닌 112동으로 정상 배달 완료된 것 맞으시죠?
RIDER: 네, 맞습니다.
AGENT: 도움 주셔서 감사합니다.
```

# 주의 사항
- AGENT 의 *첫 발화 직전* 에 load_skill 함수콜 1회가 발생 → 첫 발화까지의 latency 가 원본 대비 약간(수백 ms) 늘어난다. 그 대신 시나리오 추가/교체가 코드 수정 없이 가능.
- 시나리오 본문(`skills/*.md`) 은 그대로 모델 컨텍스트에 들어가므로, 매우 길어질 경우(>10K 토큰) 페이지네이션·검색형 로딩으로 확장하라 (wikidocs 가이드 §11 참고).
