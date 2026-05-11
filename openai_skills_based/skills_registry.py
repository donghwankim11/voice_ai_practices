"""LangChain Skills 패턴의 핵심: Skill 데이터클래스 + load_skill 도구 + 점진적 공개.

원본 Skills 가이드 (https://wikidocs.net/318950) 는 langchain.agents.create_agent +
AgentMiddleware 를 쓰지만, OpenAI Realtime API 의 양방향 스트림/서버 VAD/barge-in 과
잘 결합되지 않으므로, 본 모듈은 *패턴* 만 가져와 plain Python 으로 구현한다.

핵심 원칙:
- description: 시스템 프롬프트에 항상 노출되는 경량 한 줄 설명
- content: 사용자/시나리오 매칭 후에만 load_skill 로 온디맨드 로드되는 상세 본문
- 시나리오 본문은 skills/*.md 로 분리되어 도메인 담당자가 코드 수정 없이 편집 가능
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"


@dataclass
class Skill:
    name: str
    description: str   # 시스템 프롬프트에 들어가는 1줄 (경량)
    content: str       # load_skill 호출 시 반환되는 상세 본문 (스키마/규칙/예시)


# 시스템 프롬프트에 노출할 경량 설명만 여기서 직접 관리한다.
# (본문은 skills/<name>.md 에서 로드되어 점진적 공개됨)
_SKILL_DESCRIPTIONS: dict[str, str] = {
    "address_change_verification":
        "배달 주소 변경 확인 — 변경된 주소로 정상 배달되었는지 확인",
    "payment_missing_inquiry":
        "결제 누락 문의 — 결제가 누락된 주문 건의 경위·증빙 파악",
    "delivery_delay_compensation":
        "배달 지연 보상 — 지연 사유 청취 및 보상/면제 정책 안내",
    "coupon_refund":
        "쿠폰 환불·재정산 — 잘못 적용된 쿠폰의 재정산 의사 확인",
}


def _load(name: str, description: str) -> Skill:
    path = SKILLS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"스킬 본문 파일 없음: {path}")
    return Skill(name=name, description=description, content=path.read_text(encoding="utf-8"))


SKILLS: dict[str, Skill] = {
    name: _load(name, desc) for name, desc in _SKILL_DESCRIPTIONS.items()
}


def skill_list_for_prompt() -> str:
    """시스템 프롬프트에 박을 경량 스킬 목록 (description 만)."""
    return "\n".join(f"- {s.name}: {s.description}" for s in SKILLS.values())


def load_skill(skill_name: str) -> str:
    """LangChain @tool 의 Realtime 네이티브 등가물.

    AGENT 가 함수콜로 호출하면 본문(상세 본문)이 그대로 모델 컨텍스트에 들어간다.
    """
    if skill_name not in SKILLS:
        return (
            f"알 수 없는 스킬: {skill_name}. "
            f"사용 가능: {', '.join(SKILLS.keys())}"
        )
    return SKILLS[skill_name].content
