# LangChain Skills 패턴 요약

> 출처: https://wikidocs.net/318950

---

## 1. 개념 정의

**Skills 패턴**은 전문화된 기능을 "스킬(skill)" 단위로 패키징해서 단일 에이전트에 부착하는 패턴이다. 핵심 아이디어는 다음 네 가지.

- **프롬프트 기반 전문화** — 스킬은 코드/툴이 아니라 "전문화된 프롬프트(스키마/규칙/예시)"로 정의된다.
- **점진적 공개(Progressive Disclosure)** — 시스템 프롬프트에는 스킬 *설명*만 노출하고, 본문 *content*는 사용자가 필요로 할 때만 `load_skill` 도구로 온디맨드 로드한다.
- **팀 간 분산 개발** — 팀별로 독립적인 스킬을 개발/유지 가능.
- **경량 컴포지션** — 별도 서브에이전트를 만들지 않아 구조가 단순.

---

## 2. Skills vs Subagents vs 일반 Tools

| 구분 | Skills | Subagents |
|---|---|---|
| 구조 | 단일 에이전트 + 스킬 목록 | 감독자 + 서브에이전트 |
| 전문화 방식 | 프롬프트 | 별도 에이전트 |
| 로딩 방식 | 온디맨드 로딩 | 도구 호출 |
| 복잡도 | 낮음 | 높음 |
| 컨텍스트 | 단일 스레드 | 독립 컨텍스트 |

**일반 Tools와의 차이**: 일반 툴은 "함수 호출(input → output)"이지만, Skills는 `description`(시스템 프롬프트용 1~2줄)과 `content`(온디맨드 로드되는 상세 본문) 의 **이중 구조**를 가진다.

---

## 3. 점진적 공개 (Progressive Disclosure)

### 문제 — 모든 스키마를 처음부터 시스템 프롬프트에 넣을 때
```python
system_prompt = f"""
당신은 SQL 쿼리 작성 전문가입니다.

## 영업 분석 (2000 토큰)
{sales_schema}

## 재고 관리 (2000 토큰)
{inventory_schema}

## 고객 서비스 (2000 토큰)
{customer_service_schema}

## 재무 보고 (2000 토큰)
{finance_schema}

... (10개 더)
"""
# 총 20,000+ 토큰 — 컨텍스트 윈도우 압도, 비용 폭증, 노이즈로 성능 저하
```

### 해결책 — 설명만 노출, 본문은 온디맨드
```python
system_prompt = """
당신은 SQL 쿼리 작성 전문가입니다.

사용 가능한 스킬:
- sales_analytics: 영업 데이터 분석 (매출, 고객, 제품)
- inventory_management: 재고 추적 및 관리
- customer_service: 고객 지원 티켓 및 피드백
...

관련 스킬을 load_skill 도구로 로드하세요.
"""
# 사용자: "지난 분기 영업 실적을 보여줘"
# 에이전트: load_skill("sales_analytics") 호출 → 2000 토큰만 로드
```

**효과**: 컨텍스트 절약, 비용 절감, 집중된 컨텍스트로 성능 향상, 수십 개 스킬까지 확장 가능.

---

## 4. 구성 요소 (4가지)

1. **`Skill` 데이터클래스** — `name`, `description`(경량 설명), `content`(상세 본문)
2. **`load_skill` 도구** — 이름으로 스킬 본문을 검색해 반환
3. **`SkillMiddleware`** — `AgentMiddleware`를 상속하여 스킬 설명 목록을 시스템 프롬프트에 동적 주입
4. **에이전트** — `create_agent()`로 미들웨어/도구를 묶어 생성

---

## 5. 단계별 구현

### 5.1 스킬 정의

```python
from dataclasses import dataclass

@dataclass
class Skill:
    name: str
    description: str  # 시스템 프롬프트에 노출
    content: str      # 온디맨드 로드

SKILLS = {
    "sales_analytics": Skill(
        name="sales_analytics",
        description="영업 데이터 분석 - 매출, 고객, 제품 메트릭",
        content="""
# 영업 분석 데이터베이스 스키마

## 테이블: sales
- id (INTEGER): 주문 ID
- customer_id (INTEGER): 고객 ID
- product_id (INTEGER): 제품 ID
- amount (DECIMAL): 주문 금액
- order_date (DATE): 주문 날짜

## 테이블: customers
- id (INTEGER): 고객 ID
- name (TEXT): 고객 이름
- email (TEXT): 이메일
- created_at (DATE): 가입 날짜

## 비즈니스 규칙
- 분기는 1월(Q1), 4월(Q2), 7월(Q3), 10월(Q4) 시작
- 매출은 amount 필드 SUM으로 계산
- 반품은 amount < 0으로 표시

## 예제 쿼리
-- 지난 분기 총 매출
SELECT SUM(amount) as total_revenue
FROM sales
WHERE order_date >= DATE('now', '-3 months');
"""
    ),
    "inventory_management": Skill(
        name="inventory_management",
        description="재고 추적 및 관리 - 제품, 재고 수준, 보충",
        content="""
# 재고 관리 데이터베이스 스키마

## 테이블: products
- id (INTEGER): 제품 ID
- name (TEXT): 제품 이름
- sku (TEXT): SKU 코드
- category (TEXT): 카테고리

## 테이블: inventory
- product_id (INTEGER): 제품 ID
- warehouse_id (INTEGER): 창고 ID
- quantity (INTEGER): 재고 수량
- last_updated (TIMESTAMP): 마지막 업데이트

## 비즈니스 규칙
- 재고 부족: quantity < 10
- 과잉 재고: quantity > 1000
- 재고 회전율 = 판매량 / 평균 재고

## 예제 쿼리
-- 재고 부족 제품
SELECT p.name, i.quantity
FROM products p
JOIN inventory i ON p.id = i.product_id
WHERE i.quantity < 10;
"""
    )
}
```

### 5.2 스킬 로딩 도구

```python
from langchain.tools import tool

@tool
def load_skill(skill_name: str):
    """
    스킬 로드하여 전체 콘텐츠 가져오기

    Args:
        skill_name: 로드할 스킬 이름
    """
    if skill_name not in SKILLS:
        available = ", ".join(SKILLS.keys())
        return f"알 수 없는 스킬: {skill_name}. 사용 가능: {available}"

    skill = SKILLS[skill_name]
    return skill.content
```

### 5.3 스킬 미들웨어

```python
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from typing import Callable

class SkillMiddleware(AgentMiddleware):
    """스킬 설명을 시스템 프롬프트에 주입하는 미들웨어"""

    tools = [load_skill]   # 미들웨어가 제공하는 도구

    def __init__(self):
        self.skill_list = "\n".join([
            f"- {skill.name}: {skill.description}"
            for skill in SKILLS.values()
        ])

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        base_prompt = request.system_message or ""
        if hasattr(base_prompt, "content"):
            base_prompt = base_prompt.content

        enhanced_prompt = f"""{base_prompt}

사용 가능한 스킬:
{self.skill_list}

관련 스킬이 필요하면 load_skill 도구를 사용하세요.
"""
        request = request.override(system_prompt=enhanced_prompt)
        return handler(request)
```

### 5.4 에이전트 생성

```python
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

agent = create_agent(
    model="openai:gpt-4o-mini",
    tools=[load_skill],
    middleware=[SkillMiddleware()],
    checkpointer=InMemorySaver(),
    system_prompt="당신은 SQL 쿼리 작성 전문가입니다."
)
```

### 5.5 점진적 공개 동작 확인

```python
from langchain_core.messages import HumanMessage

config = {"configurable": {"thread_id": "user-1"}}

result = agent.invoke(
    {"messages": [HumanMessage("지난 분기 영업 실적을 보여줘")]},
    config
)
# 1. 질문 분석: "영업 실적" → sales_analytics 스킬 필요
# 2. load_skill("sales_analytics") 호출
# 3. 전체 스키마 + 비즈니스 규칙 수신
# 4. SQL 쿼리 작성
print(result["messages"][-1].content)
```

---

## 6. 고급 — 제약 조건/상태 추적

스킬 로드 여부에 따라 노출되는 도구를 동적으로 바꾸는 패턴.

```python
from langchain.agents import AgentState
from langgraph.types import Command

# 1. 커스텀 상태
class SQLAssistantState(AgentState):
    loaded_skills: list[str]

# 2. 상태 업데이트하는 load_skill
@tool
def load_skill_with_state(skill_name: str):
    """스킬 로드 및 상태 업데이트"""
    if skill_name not in SKILLS:
        return "알 수 없는 스킬"
    skill = SKILLS[skill_name]
    return Command(update={"loaded_skills": [skill_name]})

# 3. 제약된 도구
@tool
def write_sql_query(query: str):
    """SQL 쿼리 검증 및 실행 (스킬을 먼저 로드해야 함)"""
    return f"쿼리 실행 결과: {query}"

# 4. 상태 기반 미들웨어
class ConstrainedSkillMiddleware(AgentMiddleware):
    tools = [load_skill_with_state]
    state_schema = SQLAssistantState

    def wrap_model_call(self, request, handler):
        loaded = request.state.get("loaded_skills", [])
        tools = list(request.tools or [])
        if loaded:
            tools.append(write_sql_query)
        request = request.override(tools=tools)
        return handler(request)

# 5. 에이전트
agent = create_agent(
    model="openai:gpt-4o-mini",
    tools=[load_skill_with_state],
    middleware=[ConstrainedSkillMiddleware()],
    checkpointer=InMemorySaver()
)
```

---

## 7. 패턴 확장

### 동적 도구 등록 (스킬별 추가 도구)

```python
@tool
def backup_database():
    """데이터베이스 백업 (database_admin 스킬 필요)"""
    return "백업 완료"

@tool
def restore_database():
    """데이터베이스 복원 (database_admin 스킬 필요)"""
    return "복원 완료"

SKILL_TOOLS = {
    "database_admin": [backup_database, restore_database],
}

@tool
def load_skill_with_tools(skill_name: str):
    """스킬 및 관련 도구 로드"""
    if skill_name not in SKILLS:
        return "알 수 없는 스킬"
    return Command(update={"loaded_skills": [skill_name]})
```

### 계층적 스킬 (상위 → 하위)

```python
SKILLS["data_science"] = Skill(
    name="data_science",
    description="데이터 과학 - pandas, 시각화, 통계",
    content="""
데이터 과학 스킬 로드됨.

하위 스킬:
- pandas_expert: pandas 데이터 조작
- visualization: matplotlib/seaborn 차트
- statistical_analysis: 통계 분석

필요한 하위 스킬을 load_skill로 로드하세요.
"""
)

SKILLS["pandas_expert"] = Skill(
    name="pandas_expert",
    description="pandas 데이터 조작 전문가",
    content="pandas DataFrame, Series, groupby, merge 등 상세 가이드..."
)
```

### Few-Shot 예제와 결합

```python
@tool
def load_skill_with_examples(skill_name: str, user_query: str):
    """스킬 + 사용자 쿼리와 유사한 예제를 함께 로드"""
    skill = SKILLS[skill_name]
    examples = search_similar_examples(user_query, skill_name)  # 직접 구현
    content = f"""{skill.content}

관련 예제:
{format_examples(examples)}
"""
    return content
```

---

## 8. 완전한 SQL 비서 예제

```python
from dataclasses import dataclass
from typing import Dict, Callable
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain.tools import tool
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.checkpoint.memory import InMemorySaver

# 1. 스킬 정의
@dataclass
class Skill:
    name: str
    description: str
    content: str

SKILLS: Dict[str, Skill] = {
    "sales_analytics": Skill(
        name="sales_analytics",
        description="영업 데이터 분석 - 매출, 고객, 제품",
        content="""
데이터베이스: sales.db

테이블: sales (id, customer_id, product_id, amount, order_date)
테이블: customers (id, name, email, created_at)

비즈니스 규칙:
- 분기: Q1(1-3월), Q2(4-6월), Q3(7-9월), Q4(10-12월)
- 매출 계산: SUM(amount)

예제:
SELECT SUM(amount) FROM sales WHERE order_date >= '2024-01-01';
"""
    ),
    "inventory_management": Skill(
        name="inventory_management",
        description="재고 추적 및 관리 - 제품, 재고, 보충",
        content="""
데이터베이스: inventory.db

테이블: products (id, name, sku, category)
테이블: inventory (product_id, warehouse_id, quantity, last_updated)

비즈니스 규칙:
- 재고 부족: quantity < 10
- 과잉 재고: quantity > 1000

예제:
SELECT name, quantity FROM products p
JOIN inventory i ON p.id = i.product_id
WHERE quantity < 10;
"""
    )
}

# 2. 스킬 로딩 도구
@tool
def load_skill(skill_name: str):
    """스킬 로드"""
    if skill_name not in SKILLS:
        return f"알 수 없는 스킬. 사용 가능: {', '.join(SKILLS.keys())}"
    return SKILLS[skill_name].content

# 3. 미들웨어
class SkillMiddleware(AgentMiddleware):
    tools = [load_skill]

    def __init__(self):
        self.skill_list = "\n".join([
            f"- {s.name}: {s.description}" for s in SKILLS.values()
        ])

    def wrap_model_call(self, request, handler):
        base = request.system_message or ""
        if hasattr(base, "content"):
            base = base.content
        enhanced = f"""{base}

사용 가능한 스킬:
{self.skill_list}

관련 스킬을 load_skill로 로드하세요.
"""
        request = request.override(system_prompt=enhanced)
        return handler(request)

# 4. 에이전트
agent = create_agent(
    model="openai:gpt-4o-mini",
    tools=[load_skill],
    middleware=[SkillMiddleware()],
    checkpointer=InMemorySaver(),
    system_prompt="SQL 쿼리 작성 전문가"
)

# 5. 사용
config = {"configurable": {"thread_id": "session-1"}}
result = agent.invoke(
    {"messages": [HumanMessage("재고 부족 제품을 보여줘")]},
    config
)
print(result["messages"][-1].content)
```

---

## 9. 최소 구현 (Boilerplate)

```python
from dataclasses import dataclass
from typing import Callable
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain.tools import tool
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langgraph.checkpoint.memory import InMemorySaver

@dataclass
class Skill:
    name: str
    description: str
    content: str

SKILLS = {
    "sales": Skill("sales", "영업 분석", "테이블: sales, customers..."),
    "inventory": Skill("inventory", "재고 관리", "테이블: products, inventory..."),
}

@tool
def load_skill(name: str):
    """스킬 로드"""
    return SKILLS[name].content if name in SKILLS else "알 수 없는 스킬"

class SkillMiddleware(AgentMiddleware):
    tools = [load_skill]

    def __init__(self):
        self.skills = "\n".join(f"- {s.name}: {s.description}" for s in SKILLS.values())

    def wrap_model_call(self, request, handler):
        prompt = f"{request.system_message or ''}\n\n스킬:\n{self.skills}\n\nload_skill 사용."
        return handler(request.override(system_prompt=prompt))

agent = create_agent(
    model="openai:gpt-4o-mini",
    tools=[load_skill],
    middleware=[SkillMiddleware()],
    checkpointer=InMemorySaver()
)

result = agent.invoke(
    {"messages": [HumanMessage("영업 데이터를 보여줘")]},
    {"configurable": {"thread_id": "1"}}
)
print(result["messages"][-1].content)
```

---

## 10. 적합한 사용 케이스

- 단일 에이전트가 **여러 전문 영역**을 다뤄야 할 때
- 스킬 간 **강한 제약/격리**가 필요 없을 때
- 서로 다른 **팀이 독립적으로** 기능을 추가해야 할 때
- **프롬프트 기반 전문화**만으로 충분할 때

**대표 사례**:
- 코딩 어시스턴트 — 언어/작업별 스킬 (Python, JS, 디버깅, 리팩토링)
- 지식베이스 — 도메인별 스킬 (영업/재고/CS)
- 크리에이티브 어시스턴트 — 포맷별 스킬 (블로그/이메일/소셜)

---

## 11. 구현 옵션

### 스토리지 백엔드
- **메모리** — Python dict, 빠르지만 휘발성
- **파일 시스템** — 디렉토리/파일, Claude Code 스타일
- **원격** — S3, DB, Notion, API에서 동적으로 가져오기

### 스킬 발견
- **시스템 프롬프트 나열** — 본 튜토리얼 방식
- **파일 기반** — 디렉토리 스캔
- **레지스트리** — 스킬 레지스트리 API
- **동적 조회** — `list_skills` 같은 도구 호출

### 크기 가이드
| 크기 | 토큰 | 단어 | 권장 전략 |
|---|---|---|---|
| 소형 | < 1K | ~750 | 시스템 프롬프트 직접 포함 + 캐싱 |
| 중형 | 1~10K | ~750~7.5K | 온디맨드 로딩 (본 튜토리얼) |
| 대형 | > 10K | > 7.5K | 페이지네이션, 검색, 계층 탐색 |

---

## 12. 핵심 학습 포인트

1. **점진적 공개** — 필요한 정보만 온디맨드 로드해 컨텍스트 절약
2. **프롬프트 기반** — 스킬은 완전한 에이전트가 아닌 *전문화 프롬프트*
3. **AgentMiddleware** — 클래스 기반 미들웨어로 시스템 프롬프트에 동적 주입
4. **확장성** — 컨텍스트 오버로드 없이 수십 개 스킬 추가 가능
5. **팀 자율성** — 팀별 독립 개발/유지
