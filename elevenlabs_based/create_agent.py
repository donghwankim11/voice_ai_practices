"""ElevenLabs Conversational AI 데모용 agent 를 생성/업데이트합니다.

사용:
    python create_agent.py

동작:
    - .env 의 ELEVENLABS_API_KEY 로 ElevenLabs REST API 호출
    - .env 에 ELEVENLABS_AGENT_ID 가 없으면: 신규 agent 생성 + .env 에 저장
    - .env 에 ELEVENLABS_AGENT_ID 가 있으면: 해당 agent 를 본 파일의 설정으로 업데이트(PATCH)
      → LLM/voice/turn_timeout 등 코드 변경을 즉시 반영할 수 있음
"""
from __future__ import annotations

import json
import os
import ssl
import sys
from pathlib import Path

import httpx
import truststore
from dotenv import load_dotenv

from scenario import OPENING_LINE, SCENARIO_PROMPT

# 사내 프록시(SSL 인스펙션) 환경 대응: OS 키체인 사용
SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

# ---------------------------------------------------------------------------
# 환경 변수
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent
ENV_PATH = HERE / ".env"
load_dotenv(ENV_PATH)

API_KEY = os.getenv("ELEVENLABS_API_KEY")
EXISTING_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID")

# 기본 다국어 voice — Rachel (모든 계정 기본 라이브러리에 존재).
# 한국어 자연스러운 다른 voice 가 있으면 .env 에 ELEVENLABS_VOICE_ID 로 override.
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

if not API_KEY:
    print("[ERROR] .env 에 ELEVENLABS_API_KEY 가 없습니다.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Agent 설정 (생성/업데이트 공용 페이로드)
# ---------------------------------------------------------------------------
# platform_settings.overrides 로 conversation 시작 시 prompt/first_message/language
# 를 코드 쪽에서 override 할 수 있도록 권한을 켜둔다 (app_realtime.py 가 활용).
agent_config = {
    "name": "Baemin 고객센터 AGENT (Demo)",
    "conversation_config": {
        "agent": {
            "prompt": {
                "prompt": SCENARIO_PROMPT,
                # 내부 LLM 을 Gemini 2.5 Flash Lite 로 지정 — TTFT 가 매우 짧아
                # 실시간 음성 통화에서 첫 응답 체감 속도 개선 효과가 큼.
                # (모델 슬러그가 계정/시점에 따라 다를 수 있으면 대시보드의
                #  Agent → LLM 드롭다운에서 표시되는 정확한 값을 확인할 것.)
                "llm": "gemini-2.5-flash-lite",
            },
            "first_message": OPENING_LINE,
            "language": "ko",
        },
        "tts": {
            "voice_id": VOICE_ID,
            # flash_v2_5: turbo 보다 한 단계 더 빠른 저지연 모델 (TTFB 감소).
            "model_id": "eleven_flash_v2_5",
            "voice_settings": {
                # 1.0 기본, 0.7~1.2 범위. AGENT 발화 속도를 약간 빠르게.
                "speed": 1.15,
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        },
        "asr": {
            "quality": "high",
            "user_input_audio_format": "pcm_16000",
        },
        # 턴 종료 감지를 짧게 잡아 RIDER 발화 표시와 AGENT 응답을 빠르게 만든다.
        # 기본값(약 7초)이면 RIDER 가 말을 마친 뒤 7초 무음을 기다린 후에야
        # user_transcript 가 발사되고 AGENT 도 응답을 시작한다.
        # ElevenLabs API 제약: turn_timeout 은 -1(=무제한) 또는 1~300 사이.
        "turn": {
            "turn_timeout": 1.0,
            "mode": "turn",
        },
    },
    "platform_settings": {
        "overrides": {
            "conversation_config_override": {
                "agent": {
                    "prompt": {"prompt": True},
                    "first_message": True,
                    "language": True,
                },
                # 세션 단위로도 turn_timeout / TTS 속도를 조정할 수 있도록 권한 부여
                "turn": {
                    "turn_timeout": True,
                },
                "tts": {
                    "voice_settings": True,
                },
            },
        },
    },
}

if EXISTING_AGENT_ID:
    # 기존 agent 의 설정만 갱신 (LLM/voice/turn_timeout 등 코드 변경 반영)
    print(f"[INFO] ElevenLabs agent 업데이트 요청 → {EXISTING_AGENT_ID}")
    resp = httpx.patch(
        f"https://api.elevenlabs.io/v1/convai/agents/{EXISTING_AGENT_ID}",
        headers={"xi-api-key": API_KEY, "Content-Type": "application/json"},
        json=agent_config,
        timeout=30.0,
        verify=SSL_CTX,
    )
    if resp.status_code != 200:
        print(f"[ERROR] {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] agent 업데이트 완료: {EXISTING_AGENT_ID}")
    print()
    print("이제 다음을 실행하세요:")
    print("    python app_realtime.py")
    sys.exit(0)


print(f"[INFO] ElevenLabs agent 생성 요청 (voice_id={VOICE_ID}) ...")
resp = httpx.post(
    "https://api.elevenlabs.io/v1/convai/agents/create",
    headers={"xi-api-key": API_KEY, "Content-Type": "application/json"},
    json=agent_config,
    timeout=30.0,
    verify=SSL_CTX,
)

if resp.status_code != 200:
    print(f"[ERROR] {resp.status_code}: {resp.text}", file=sys.stderr)
    sys.exit(1)

data = resp.json()
agent_id = data.get("agent_id") or data.get("id")
if not agent_id:
    print(f"[ERROR] 응답에 agent_id 가 없습니다: {data}", file=sys.stderr)
    sys.exit(1)

print(f"[OK] agent_id = {agent_id}")

# ---------------------------------------------------------------------------
# .env 갱신 (없으면 생성)
# ---------------------------------------------------------------------------
existing = ENV_PATH.read_text() if ENV_PATH.exists() else ""

if "ELEVENLABS_AGENT_ID=" in existing:
    new_lines = []
    for line in existing.splitlines():
        if line.startswith("ELEVENLABS_AGENT_ID="):
            new_lines.append(f"ELEVENLABS_AGENT_ID={agent_id}")
        else:
            new_lines.append(line)
    ENV_PATH.write_text("\n".join(new_lines) + ("\n" if existing.endswith("\n") else ""))
else:
    suffix = "" if existing.endswith("\n") or not existing else "\n"
    ENV_PATH.write_text(existing + suffix + f"ELEVENLABS_AGENT_ID={agent_id}\n")

print(f"[OK] .env 에 ELEVENLABS_AGENT_ID 저장 완료 → {ENV_PATH}")
print()
print("이제 다음을 실행하세요:")
print("    python app_realtime.py")
