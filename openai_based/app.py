"""배달의민족 고객센터 AGENT ↔ RIDER 실시간 음성 통화 시뮬레이터 (Gradio + 서버측 VAD).

실행:
    python app.py
    # → http://localhost:7860 접속

흐름 (실시간 / VAD 기반, Realtime API 미사용):
    [Record 버튼 클릭] → 마이크 스트림 시작 (브라우저 → 서버, 약 0.3s 청크)
    [통화 시작]        → AGENT 첫 발화 자동 재생
        ↓
    사용자 발화 → 서버측 에너지 기반 VAD 가 음성 구간 검출
        ↓
    무음이 SILENCE_END_SEC 이상 지속되면 발화 종료로 판단
        ↓
    STT(gpt-4o-mini-transcribe) → LLM(gpt-5.4-mini) → TTS(gpt-4o-mini-tts)
        ↓
    AGENT 응답 자동 재생 → 재생 완료 시 다시 청취 모드로
        ↓ (반복)

⚠ 헤드폰 사용 권장 — 스피커로 들으면 AGENT 음성이 마이크로 들어가 자가 인식 루프가 발생할 수 있음.
"""
from __future__ import annotations

import os
import tempfile
import uuid
import wave
from pathlib import Path

import gradio as gr
import numpy as np
from dotenv import load_dotenv
from openai import AzureOpenAI

# ---------------------------------------------------------------------------
# Azure OpenAI 클라이언트 + 배포명 (test_openai_apis.ipynb 와 동일)
# ---------------------------------------------------------------------------
load_dotenv()

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version="2025-04-01-preview",
)

STT_DEPLOYMENT = "gpt-4o-mini-transcribe"
TTS_DEPLOYMENT = "gpt-4o-mini-tts"
LLM_DEPLOYMENT = "gpt-5.4-mini"

TTS_VOICE = "alloy"
TTS_INSTRUCTIONS = (
    "한국어 고객센터 상담사 톤으로 또렷하고 친절하게, "
    "전화 통화처럼 자연스럽고 약간 빠른 속도로, 또박또박 끊김 없이 읽어주세요."
)

# ---------------------------------------------------------------------------
# VAD 파라미터 (에너지 기반 — 환경에 맞게 ENERGY_THRESHOLD 조정 권장)
# ---------------------------------------------------------------------------
ENERGY_THRESHOLD = 0.020   # RMS 임계값 (float32 -1~1 기준). 0.01~0.05 사이에서 튜닝.
SILENCE_END_SEC = 0.45     # 이만큼 정적이 지속되면 사용자 발화 종료로 판단 (0.3~0.6 권장)
MIN_SPEECH_SEC = 0.3       # 최소 발화 길이 (이보다 짧으면 잡음으로 무시)
MAX_SPEECH_SEC = 20.0      # 한 턴 최대 길이 (안전 상한)
STREAM_EVERY = 0.3         # 마이크 스트리밍 간격(초)

# ---------------------------------------------------------------------------
# 시나리오 — AGENT 의 역할/목표/톤
# ---------------------------------------------------------------------------
SCENARIO_SYSTEM_PROMPT = """\
당신은 '배달의민족(우아한형제들)' 고객센터 상담사 'AGENT' 입니다.
지금 배달원(RIDER)에게 전화를 걸어, 한 주문 건의 배달 주소가
기존 102동에서 변경된 112동으로 바르게 배달되었는지 확인해야 합니다.

[대화 목표 — 다음 2개 사실만 각각 한 번씩 검증]
1. RIDER 가 사장님으로부터 변경된 주소(102동 → 112동)를 전달받았는가
2. 그 변경된 112동으로 음식이 정상 배달 완료되었는가
   ※ 이 한 질문으로 "RIDER 의 112동 도착", "음식 전달", "고객 수령" 을 *동시에* 확인합니다. 절대 분리해 묻지 마세요.
2개 사실이 확인되면 종합 재확인 없이 바로 감사 인사로 통화를 종료합니다.

[질문 규칙 — 매우 중요]
- 위 2개 사실 외에는 묻지 마세요. 통화 전체에서 검증 질문은 *최대 2개* 입니다.
- 같은 사실을 표현만 바꿔 다시 묻지 마세요. 다음은 모두 *동일한 사실(=배달 완료)* 이므로 둘 이상 물으면 중복입니다:
  · "112동으로 배달하셨나요?"
  · "112동으로 이동해서 배달하신 게 맞나요?"
  · "음식이 고객님께 정상 전달되었나요?"
  · "고객이 음식을 수령했나요?"
- RIDER 의 한 답변에 여러 사실이 동시에 확인되면, 모두 검증된 것으로 간주하고 다음 사실로 넘어가거나(필요시) 바로 종료합니다.
- 모든 사실이 확인된 직후에는 종합 재확인 질문을 *추가하지 말고* 바로 감사 인사로 종료합니다.

[톤/스타일]
- 한국어 존댓말, 정중하고 간결하게.
- 한 응답에 한 가지만 묻고, RIDER 의 답을 들은 뒤 다음 질문으로 넘어갑니다.
- 마무리 후에는 새로운 질문을 만들지 말고 통화를 종료합니다.

[출력 형식 — 매우 중요]
- 출력은 그대로 TTS 로 음성 합성됩니다.
- markdown, 글머리표(-, *, 숫자.), 이모지, 괄호 부연설명 사용 금지.
- 한 응답은 1~3문장 이내로 자연스러운 통화 어조로 작성합니다.
"""

OPENING_LINE = (
    "안녕하세요, 배달의민족 고객센터입니다. "
    "주문 건 배달 주소 확인차 연락드렸습니다. 잠시 통화 가능하실까요?"
)

# 임시 오디오 파일 디렉터리
TMP_DIR = Path(tempfile.gettempdir()) / "baemin_voice_agent"
TMP_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Azure OpenAI 호출 헬퍼
# ---------------------------------------------------------------------------
def synthesize_tts(text: str) -> str:
    """텍스트 → MP3 파일 경로 (gpt-4o-mini-tts)."""
    out_path = TMP_DIR / f"agent_{uuid.uuid4().hex}.mp3"
    with client.audio.speech.with_streaming_response.create(
        model=TTS_DEPLOYMENT,
        voice=TTS_VOICE,
        input=text,
        instructions=TTS_INSTRUCTIONS,
        response_format="mp3",
    ) as response:
        response.stream_to_file(out_path)
    return str(out_path)


def transcribe(audio_path: str) -> str:
    """음성 파일 → 한국어 텍스트 (gpt-4o-mini-transcribe)."""
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model=STT_DEPLOYMENT,
            file=f,
            language="ko",
            response_format="text",
        )
    text = result if isinstance(result, str) else getattr(result, "text", "")
    return (text or "").strip()


def llm_reply(messages: list[dict]) -> str:
    """대화 히스토리 → 다음 AGENT 발화 (gpt-5.4-mini)."""
    response = client.chat.completions.create(
        model=LLM_DEPLOYMENT,
        messages=messages,
        max_completion_tokens=512,
    )
    return (response.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# VAD / 오디오 헬퍼
# ---------------------------------------------------------------------------
def _init_vad_state() -> dict:
    return {
        "buffer": [],          # float32 mono 청크 리스트
        "sr": None,
        "speech_sec": 0.0,
        "silence_sec": 0.0,
        "in_speech": False,
        "agent_busy": False,   # AGENT 처리/재생 중에는 True (마이크 무시)
    }


def _to_mono_float(audio: np.ndarray) -> np.ndarray:
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if audio.dtype == np.int16:
        audio = audio.astype(np.float32) / 32768.0
    elif audio.dtype == np.int32:
        audio = audio.astype(np.float32) / float(2**31)
    elif audio.dtype != np.float32:
        audio = audio.astype(np.float32)
    return audio


def _history_to_chat_view(history: list[dict]) -> list[dict]:
    """history(system 포함) → Chatbot(type='messages') 표시용 목록 (system 제외)."""
    return [m for m in (history or []) if m.get("role") != "system"]


def _save_wav(audio: np.ndarray, sr: int) -> str:
    """float32 mono → 임시 WAV 파일 경로 (PCM16)."""
    out_path = TMP_DIR / f"user_{uuid.uuid4().hex}.wav"
    pcm16 = np.clip(audio * 32768.0, -32768, 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())
    return str(out_path)


# ---------------------------------------------------------------------------
# Gradio 핸들러
# ---------------------------------------------------------------------------
def start_call():
    """통화 시작 — AGENT 첫 발화 합성/재생."""
    history = [
        {"role": "system", "content": SCENARIO_SYSTEM_PROMPT},
        {"role": "assistant", "content": OPENING_LINE},
    ]
    chat_view = [{"role": "assistant", "content": OPENING_LINE}]
    audio_path = synthesize_tts(OPENING_LINE)
    vad_state = _init_vad_state()
    vad_state["agent_busy"] = True   # 첫 발화 재생 중에는 마이크 무시
    return history, chat_view, audio_path, "🔊 AGENT 발화 중...", vad_state


def reset_call():
    """초기화 — 대화/오디오/VAD 상태 모두 비움."""
    return (
        [],
        [],
        None,
        "🔄 초기화되었습니다. 마이크 [Record] → '통화 시작' 순으로 다시 시작하세요.",
        _init_vad_state(),
    )


def on_agent_audio_end(vad_state):
    """AGENT TTS 재생이 끝나면 다시 마이크 청취 모드로."""
    if not vad_state:
        vad_state = _init_vad_state()
    vad_state["agent_busy"] = False
    vad_state["buffer"] = []
    vad_state["speech_sec"] = 0.0
    vad_state["silence_sec"] = 0.0
    vad_state["in_speech"] = False
    return vad_state, "🟢 통화 중 — 자유롭게 말씀하세요. (말 멈추면 자동 종료 감지)"


def stream_audio(new_chunk, vad_state, history):
    """마이크 스트리밍 청크 콜백 — 매 STREAM_EVERY 초마다 호출.

    chatbot/history 는 변경이 있을 때만 새 값을 반환하고, 그 외에는 gr.update() 로
    유지해야 한다. (스트림 핸들러가 입력값을 그대로 출력으로 돌려쓰면, 큐잉된 청크의
    오래된 입력값이 신선한 값을 덮어쓰는 경합이 발생함.)
    """
    if vad_state is None:
        vad_state = _init_vad_state()

    # AGENT 처리/재생 중에는 마이크 입력 무시 (자가 인식 방지)
    if vad_state.get("agent_busy"):
        return vad_state, gr.update(), gr.update(), gr.update(), gr.update()

    if new_chunk is None:
        return vad_state, gr.update(), gr.update(), gr.update(), gr.update()

    sr, audio = new_chunk
    audio = _to_mono_float(audio)
    if audio.size == 0:
        return vad_state, gr.update(), gr.update(), gr.update(), gr.update()

    chunk_sec = len(audio) / float(sr)
    rms = float(np.sqrt(np.mean(audio ** 2) + 1e-12))
    is_speech = rms > ENERGY_THRESHOLD

    if vad_state["sr"] is None:
        vad_state["sr"] = sr

    if is_speech:
        vad_state["buffer"].append(audio)
        vad_state["speech_sec"] += chunk_sec
        vad_state["silence_sec"] = 0.0
        vad_state["in_speech"] = True
        # 너무 길게 말하면 강제 종료
        if vad_state["speech_sec"] >= MAX_SPEECH_SEC:
            return _finalize_turn(vad_state, history)
        return (
            vad_state, gr.update(), gr.update(), gr.update(),
            f"🎤 듣는 중... (RMS={rms:.3f}, {vad_state['speech_sec']:.1f}s)",
        )

    # 무음
    if vad_state["in_speech"]:
        vad_state["buffer"].append(audio)   # 발화 끝 살짝 포함
        vad_state["silence_sec"] += chunk_sec
        if vad_state["silence_sec"] >= SILENCE_END_SEC:
            return _finalize_turn(vad_state, history)
        return (
            vad_state, gr.update(), gr.update(), gr.update(),
            f"🎤 듣는 중... (정적 {vad_state['silence_sec']:.1f}s)",
        )

    # 발화 시작 전 정적 — 그대로 대기
    return vad_state, gr.update(), gr.update(), gr.update(), gr.update()


def _finalize_turn(vad_state, history):
    """버퍼링된 사용자 발화 → STT → LLM → TTS.

    chat_view 는 history 에서 직접 유도한다 (Chatbot 컴포넌트를 입력으로 읽지 않음 —
    스트리밍 경합으로 인한 옛 값 오염을 피하기 위함).
    """
    speech_sec = vad_state["speech_sec"]
    sr = vad_state["sr"]
    buffer = vad_state["buffer"]

    # 너무 짧으면 잡음으로 간주하고 버림
    if speech_sec < MIN_SPEECH_SEC or not buffer or sr is None:
        new_state = _init_vad_state()
        return (
            new_state, gr.update(), gr.update(), gr.update(),
            "🟢 통화 중 — 말씀하세요.",
        )

    # 처리 동안 마이크 무시
    new_state = _init_vad_state()
    new_state["agent_busy"] = True

    full_audio = np.concatenate(buffer)
    wav_path = _save_wav(full_audio, sr)

    user_text = transcribe(wav_path)
    if not user_text:
        new_state["agent_busy"] = False
        return (
            new_state, gr.update(), gr.update(), gr.update(),
            "(음성 인식 결과가 비었습니다. 다시 말씀해주세요.)",
        )

    if not history:
        history = [{"role": "system", "content": SCENARIO_SYSTEM_PROMPT}]
    history = list(history) + [{"role": "user", "content": user_text}]

    assistant_text = llm_reply(history)
    history = history + [{"role": "assistant", "content": assistant_text}]

    audio_out = synthesize_tts(assistant_text)
    return (
        new_state,
        history,
        _history_to_chat_view(history),
        audio_out,
        "🔊 AGENT 응답 재생 중...",
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
with gr.Blocks(title="Baemin 음성 AGENT 시뮬레이터 (실시간/VAD)", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        """
        # 📞 배달의민족 고객센터 음성 AGENT 시뮬레이터 (실시간 / VAD)

        STT(`gpt-4o-mini-transcribe`) + LLM(`gpt-5.4-mini`) + TTS(`gpt-4o-mini-tts`) 를
        조합한 **턴 자동 감지(VAD) 기반** 실시간 음성 대화입니다.
        Realtime API 를 쓰지 않고 세 모델을 순차 호출하지만, 사용자가 말 멈추면
        자동으로 종료를 감지하여 응답합니다.

        ### 사용 방법
        1. 🎙️ 아래 마이크 컴포넌트의 **[Record]** 버튼을 한 번 클릭하여 스트리밍을 시작합니다.
        2. **📞 통화 시작** 을 눌러 AGENT 의 첫 발화를 듣습니다.
        3. AGENT 가 말을 끝내면 자동으로 듣기 모드로 전환됩니다 — **그냥 자연스럽게 말씀하세요.**
        4. 말을 멈추고 잠깐 정적이 흐르면 자동으로 STT → LLM → TTS 처리됩니다.
        5. 모든 확인이 끝나면 AGENT 가 마무리 인사로 통화를 종료합니다.

        > ⚠️ **헤드폰 사용 강력 권장** — 스피커로 들으면 AGENT 음성이 마이크에 다시 들어가 자가 인식할 수 있습니다.
        > 환경 잡음이 많으면 코드 상단의 `ENERGY_THRESHOLD` 값을 0.01~0.05 사이에서 조정하세요.
        """
    )

    history_state = gr.State([])
    vad_state = gr.State(_init_vad_state())

    chatbot = gr.Chatbot(
        label="📋 통화 내역",
        height=300,
        type="messages",
        avatar_images=(None, None),
    )
    agent_audio = gr.Audio(
        label="🔊 AGENT 발화 (자동 재생)",
        autoplay=True,
        type="filepath",
        interactive=False,
    )
    status = gr.Markdown("🟡 대기 중 — 마이크 [Record] 후 '통화 시작' 을 눌러주세요.")

    with gr.Row():
        start_btn = gr.Button("📞 통화 시작", variant="primary", scale=2)
        reset_btn = gr.Button("🔄 초기화", scale=1)

    mic = gr.Audio(
        sources=["microphone"],
        streaming=True,
        type="numpy",
        label="🎙️ 실시간 마이크 (Record 클릭 후 자유 발화)",
    )

    start_btn.click(
        start_call,
        outputs=[history_state, chatbot, agent_audio, status, vad_state],
    )
    reset_btn.click(
        reset_call,
        outputs=[history_state, chatbot, agent_audio, status, vad_state],
    )
    mic.stream(
        stream_audio,
        inputs=[mic, vad_state, history_state],
        outputs=[vad_state, history_state, chatbot, agent_audio, status],
        stream_every=STREAM_EVERY,
    )
    agent_audio.stop(
        on_agent_audio_end,
        inputs=[vad_state],
        outputs=[vad_state, status],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
