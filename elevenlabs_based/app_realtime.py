"""배달의민족 고객센터 AGENT ↔ RIDER 실시간 음성 통화 (ElevenLabs Conversational AI).

`openai_based/app_realtime.py` (Azure OpenAI Realtime + 서버 VAD) 의 ElevenLabs 카운터파트.
ElevenLabs Conversational AI 는 STT + LLM + TTS + VAD + 인터럽션을 한 WebSocket 으로
통합 제공하므로, OpenAI Realtime 과 거의 동일한 단일 모델 / 단일 소켓 구조이다.

실행:
    python app_realtime.py
    # → http://localhost:7862

흐름:
    [브라우저 마이크 PCM16 16kHz] ──WS──▶ [FastAPI 서버] ──async WS──▶ [ElevenLabs Convai]
                                                                          │
    [브라우저 스피커 재생] ◀──WS── [FastAPI 서버] ◀──async WS── [audio event]

VAD/인터럽션:
    ElevenLabs Convai 는 서버측 VAD 가 항상 켜져 있고 인터럽션도 자동 처리된다.
    1) RIDER 가 말을 시작하면 서버가 자동으로 진행 중인 AGENT 응답을 cancel
    2) 서버가 'interruption' 이벤트를 발사하면, 본 서버는 클라이언트에 'interrupt'
       신호를 보내 이미 큐잉된 AGENT 오디오 재생을 즉시 중단시킨다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import ssl
from contextlib import suppress

import truststore
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from scenario import OPENING_LINE, SCENARIO_PROMPT

# ---------------------------------------------------------------------------
# 환경 변수
# ---------------------------------------------------------------------------
load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID")

if not ELEVENLABS_API_KEY:
    raise RuntimeError(".env 에 ELEVENLABS_API_KEY 가 필요합니다.")
if not AGENT_ID:
    raise RuntimeError(
        ".env 에 ELEVENLABS_AGENT_ID 가 필요합니다. "
        "먼저 `python create_agent.py` 를 실행해 agent 를 생성하세요."
    )

# 16kHz PCM16 mono — ElevenLabs Convai 의 기본 입력/출력 포맷.
# WebSocket URL 에 output_format 쿼리 파라미터로 출력도 PCM 으로 명시.
SAMPLE_RATE = 16000
WS_URL = (
    f"wss://api.elevenlabs.io/v1/convai/conversation"
    f"?agent_id={AGENT_ID}"
    f"&output_format=pcm_{SAMPLE_RATE}"
)

# 사내 프록시(SSL 인스펙션) 환경에서 발생하는
#   "self-signed certificate in certificate chain" 오류 대응.
# certifi 번들 대신 OS 시스템 트러스트 스토어(=macOS 키체인)를 사용해
# 사내 CA 를 자동으로 신뢰한다.
SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

# 시나리오 상수(OPENING_LINE / SCENARIO_PROMPT)는 scenario.py 에서 import.


# ---------------------------------------------------------------------------
# Basic Auth (Railway 등 공개 URL 보호용)
# ---------------------------------------------------------------------------
BASIC_USER = os.getenv("BASIC_AUTH_USER")
BASIC_PASS = os.getenv("BASIC_AUTH_PASS")


def _check_basic_auth(header_value: str | None) -> bool:
    if not (BASIC_USER and BASIC_PASS):
        return True  # 환경변수 미설정 시 비활성 (로컬 개발 편의)
    if not header_value or not header_value.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header_value.split(" ", 1)[1]).decode("utf-8")
        user, _, pw = decoded.partition(":")
    except Exception:
        return False
    return secrets.compare_digest(user, BASIC_USER) and secrets.compare_digest(pw, BASIC_PASS)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if _check_basic_auth(request.headers.get("authorization")):
            return await call_next(request)
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="baemin-elevenlabs"'},
        )


# ---------------------------------------------------------------------------
# FastAPI 앱
# ---------------------------------------------------------------------------
app = FastAPI(title="Baemin 음성 AGENT (ElevenLabs Convai)")
app.add_middleware(BasicAuthMiddleware)


@app.get("/")
async def root():
    return HTMLResponse(INDEX_HTML)


@app.websocket("/ws")
async def ws_endpoint(client_ws: WebSocket):
    """브라우저 ↔ ElevenLabs Convai 브릿지."""
    await client_ws.accept()
    print("[ws] client connected")

    try:
        async with websockets.connect(
            WS_URL,
            additional_headers={"xi-api-key": ELEVENLABS_API_KEY},
            max_size=2**24,
            ssl=SSL_CTX,
        ) as eleven:
            # 세션 시작 시 시나리오/첫 발화/언어를 override 로 전달.
            # (agent 자체에 이미 동일 prompt 가 저장돼 있어도, 코드 수정 시 즉시
            #  반영되도록 매 세션 override 한다. agent 의 platform_settings 에서
            #  override 권한이 켜져 있어야 함 — create_agent.py 에서 자동 설정.)
            init_msg = {
                "type": "conversation_initiation_client_data",
                "conversation_config_override": {
                    "agent": {
                        "prompt": {"prompt": SCENARIO_PROMPT},
                        "first_message": OPENING_LINE,
                        "language": "ko",
                    },
                    # RIDER 발화 종료 후 화면 표출 / AGENT 응답을 빠르게 하기 위해
                    # 턴 종료 감지 임계 시간을 1.0초로 단축. (기본 ~7s → 1.0s)
                    # 너무 줄이면 짧은 호흡에도 끊겨 인터럽션이 잦아질 수 있음.
                    "turn": {
                        "turn_timeout": 1.0,
                    },
                    # AGENT 발화 속도를 약간 빠르게.
                    "tts": {
                        "voice_settings": {
                            "speed": 1.15,
                            "stability": 0.5,
                            "similarity_boost": 0.75,
                        },
                    },
                },
            }
            await eleven.send(json.dumps(init_msg))

            async def from_client():
                """브라우저 → ElevenLabs 마이크 PCM 전달."""
                while True:
                    raw = await client_ws.receive_text()
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    mtype = msg.get("type")
                    if mtype == "audio":
                        # ElevenLabs 는 base64 PCM16 16kHz 를 user_audio_chunk 로 받음.
                        await eleven.send(json.dumps({
                            "user_audio_chunk": msg["audio"],
                        }))
                    elif mtype == "stop":
                        return

            async def from_eleven():
                """ElevenLabs → 브라우저 오디오/텍스트/이벤트 중계."""
                async for raw in eleven:
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    etype = ev.get("type")

                    if etype == "audio":
                        b64 = ev.get("audio_event", {}).get("audio_base_64", "")
                        if b64:
                            await client_ws.send_text(json.dumps({
                                "type": "audio_delta",
                                "audio": b64,
                            }))

                    elif etype == "agent_response":
                        text = (
                            ev.get("agent_response_event", {})
                            .get("agent_response", "")
                        )
                        await client_ws.send_text(json.dumps({
                            "type": "agent_text_done",
                            "transcript": text,
                        }))

                    elif etype == "agent_response_correction":
                        # 인터럽션 후 모델이 자기 발화를 잘라낸 corrected text.
                        corrected = (
                            ev.get("agent_response_correction_event", {})
                            .get("corrected_agent_response", "")
                        )
                        if corrected:
                            await client_ws.send_text(json.dumps({
                                "type": "agent_text_done",
                                "transcript": corrected,
                            }))

                    elif etype == "user_transcript":
                        text = (
                            ev.get("user_transcription_event", {})
                            .get("user_transcript", "")
                        )
                        await client_ws.send_text(json.dumps({
                            "type": "user_text_done",
                            "transcript": text,
                        }))

                    elif etype == "interruption":
                        # 사용자가 끼어들어 AGENT 발화가 cancel 됨.
                        # 클라이언트 재생 큐 즉시 비우기.
                        await client_ws.send_text(json.dumps({"type": "interrupt"}))

                    elif etype == "ping":
                        # keep-alive: pong 회신 필수
                        ping_id = ev.get("ping_event", {}).get("event_id")
                        if ping_id is not None:
                            await eleven.send(json.dumps({
                                "type": "pong",
                                "event_id": ping_id,
                            }))

                    elif etype == "conversation_initiation_metadata":
                        meta = ev.get("conversation_initiation_metadata_event", {})
                        print(f"[eleven] session started: {meta}")

                    elif etype == "internal_vad_score":
                        # 디버깅용 — 필요 없으면 무시
                        pass

                    elif etype == "client_tool_call":
                        # 본 데모는 tool call 미사용. 무시.
                        pass

            client_task = asyncio.create_task(from_client())
            eleven_task = asyncio.create_task(from_eleven())

            done, pending = await asyncio.wait(
                {client_task, eleven_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t

    except WebSocketDisconnect:
        print("[ws] client disconnected")
    except Exception as e:
        print(f"[ws] error: {e!r}")
        with suppress(Exception):
            await client_ws.send_text(json.dumps({
                "type": "error",
                "message": f"{type(e).__name__}: {e}",
            }))


# ---------------------------------------------------------------------------
# 브라우저 페이지 (단일 HTML, AudioWorklet 인라인)
# - PCM16 mono 16kHz 입출력 (OpenAI 24kHz 버전과 다름)
# - 40ms 청크 = 640 샘플
# ---------------------------------------------------------------------------
INDEX_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>📞 Baemin 음성 AGENT (ElevenLabs Convai)</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 760px; margin: 32px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 22px; margin-bottom: 8px; }
  .desc { color: #555; line-height: 1.55; font-size: 14px; }
  .controls { margin: 18px 0 12px; display: flex; gap: 8px; align-items: center; }
  button { padding: 10px 18px; font-size: 14px; cursor: pointer;
           border: 1px solid #ccc; border-radius: 6px; background: #fff; }
  button.primary { background: #2563eb; color: #fff; border-color: #2563eb; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  #status { font-size: 13px; padding: 4px 10px; border-radius: 4px;
            background: #eee; color: #333; }
  #status.live { background: #d1fae5; color: #065f46; }
  #status.error { background: #fee2e2; color: #991b1b; }
  /* 브라우저 STT 동작 여부를 즉시 보여주는 뱃지 */
  #sttBadge { font-size: 12px; padding: 3px 8px; border-radius: 4px;
              background: #e5e7eb; color: #6b7280; }
  #sttBadge.on    { background: #d1fae5; color: #065f46; }
  #sttBadge.off   { background: #fee2e2; color: #991b1b; }
  #sttBadge.error { background: #fef3c7; color: #92400e; }
  #log { margin-top: 16px; padding: 12px; background: #f9fafb;
         border: 1px solid #e5e7eb; border-radius: 8px;
         max-height: 460px; overflow-y: auto; }
  .msg { margin: 6px 0; padding: 8px 12px; border-radius: 8px;
         font-size: 14px; line-height: 1.5; }
  .msg .role { font-size: 11px; font-weight: 600; color: #6b7280;
               margin-bottom: 2px; letter-spacing: 0.04em; }
  .msg.agent { background: #eff6ff; }
  .msg.agent .role { color: #1d4ed8; }
  /* AGENT 가 발화 중인데 텍스트가 아직 안 도착한 placeholder */
  .msg.agent.pending { background: #f1f5f9; color: #6b7280; font-style: italic; }
  .msg.user  { background: #fef9c3; }
  .msg.user .role { color: #854d0e; }
  /* 브라우저 STT 임시 결과 — 확정 전이라 약간 옅게 */
  .msg.user.interim { background: #fefce8; color: #6b7280; font-style: italic; }
  .hint { color: #6b7280; font-size: 12px; margin-top: 8px; }
</style>
</head>
<body>
<h1>📞 배달의민족 고객센터 음성 AGENT — ElevenLabs Convai</h1>
<p class="desc">
  AI 상담사(AGENT)가 배달원(RIDER)에게 전화를 걸어,
  배달 주소 변경 건(102동 → 112동)의 정상 배달 여부를 확인합니다.<br/>
  AGENT 발화 중에도 끼어들어 말할 수 있습니다 — <b>또렷하고 충분히 큰 목소리</b>로 말씀하시면
  자동으로 AGENT 발화를 끊고 듣기 모드로 전환됩니다 (에코 차단을 위한 게이트가 있어 작은 소리는 무시됩니다).
</p>

<div class="controls">
  <button id="startBtn" class="primary">📞 통화 시작</button>
  <button id="stopBtn" disabled>⏹ 통화 종료</button>
  <span id="status">대기 중</span>
  <span id="sttBadge">🎤 STT 대기</span>
</div>
<div class="hint">처음 접속 시 브라우저가 마이크 권한을 묻습니다. 허용해 주세요.<br/>
🎧 <b>이어폰 / 헤드셋 사용 권장</b> — 내장 스피커 + 마이크 환경에선 AGENT 음성이 마이크로 다시 들어가 RIDER 발화로 잘못 인식될 수 있습니다.</div>

<div id="log"></div>

<script>
let ws = null;
let audioCtx = null;
let mediaStream = null;
let micSource = null;
let workletNode = null;
// 브라우저 STT (Web Speech API). RIDER 가 말하는 즉시 화면에 흘려주기 위함.
// ElevenLabs 의 user_transcript(권위) 가 도착하면 그 텍스트로 교체한다.
let recognition = null;
let interimBubble = null;
// AGENT 발화 시작 시 미리 만들어 두는 빈 메시지 자리. agent_response 텍스트가
// 늦게 도착해도 이 자리에 채워지므로 화면 순서가 RIDER 답변과 뒤섞이지 않는다.
let agentPlaceholder = null;

// ------- 에코 차단 정책 -------
// 내장 스피커 → 마이크 회귀(에코) 가 RIDER 발화로 잘못 인식되는 것을 막기 위해
// AGENT 발화 중에는 마이크 입력을 게이팅한다.
//   ECHO_HARD_MUTE = true  : AGENT 발화 중 mic 송신 / 브라우저 STT 완전 정지
//                            → 에코 누설 0%, 단 AGENT 발화 중 끼어들기 불가
//   ECHO_HARD_MUTE = false : 큰 소리(>= ECHO_INTERRUPT_THRESHOLD)만 통과
//                            → 끼어들기 가능, 에코 일부 누설 가능
//                            (브라우저 STT 는 계속 동작해 streaming 표시 유지)
const ECHO_HARD_MUTE = false;
const ECHO_INTERRUPT_THRESHOLD = 0.18;  // ECHO_HARD_MUTE=false 일 때만 사용

function rmsEnergyInt16(buf) {
  const view = new Int16Array(buf);
  if (view.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < view.length; i++) {
    const s = view[i] / 32768;
    sum += s * s;
  }
  return Math.sqrt(sum / view.length);
}

// 재생 큐: 모든 BufferSourceNode 를 보관 → interrupt 시 일괄 stop.
// `speaking` 상태 전이를 추적해 mic 송신 / 브라우저 STT 게이팅에 사용.
const playback = {
  sources: [],
  nextStart: 0,
  speaking: false,
  setSpeaking(v) {
    if (this.speaking === v) return;
    this.speaking = v;
    if (v) {
      // AGENT 발화 시작 → 화면에 빈 placeholder 를 미리 만들어 RIDER 메시지와의
      // 순서를 보장. 비어 있던 placeholder 가 남아 있으면(=텍스트 미도착) 정리.
      if (agentPlaceholder && !agentPlaceholder.querySelector(".text").textContent) {
        agentPlaceholder.remove();
      }
      agentPlaceholder = addMsg("agent", "···");
      agentPlaceholder.classList.add("pending");
    }
    // ECHO_HARD_MUTE 모드에서만 브라우저 STT 도 함께 일시정지/재개 (에코 차단)
    if (ECHO_HARD_MUTE && recognition) {
      if (v) {
        try { recognition.stop(); } catch(_) {}
        setSttBadge("🎤 STT 일시정지", "");
      } else {
        setTimeout(() => {
          if (!recognition || playback.speaking) return;
          try { recognition.start(); } catch(_) { /* already started 가능 */ }
        }, 250);
      }
    }
  },
  reset() {
    for (const s of this.sources) { try { s.stop(); } catch(_){} }
    this.sources = [];
    this.nextStart = 0;
    this.setSpeaking(false);
  },
};
function agentIsSpeaking() { return playback.speaking; }

const startBtn = document.getElementById("startBtn");
const stopBtn  = document.getElementById("stopBtn");
const statusEl = document.getElementById("status");
const sttBadge = document.getElementById("sttBadge");
const logEl    = document.getElementById("log");

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}

function setSttBadge(text, cls) {
  sttBadge.textContent = text;
  sttBadge.className = cls || "";
}

function addMsg(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.innerHTML = `<div class="role">${role === "agent" ? "AGENT" : "RIDER"}</div><div class="text"></div>`;
  div.querySelector(".text").textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}

// ---------- 브라우저 STT (Web Speech API) ----------
// 사용자가 말하기 시작하자마자 interim 결과를 화면에 즉시 표시한다.
// ElevenLabs 의 최종 user_transcript 가 도착하면 그 텍스트로 교체.
// (Chrome/Edge/Safari 지원, Firefox 미지원 — 미지원이면 그냥 비활성)
function setupRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    console.warn("[SR] Web Speech API not supported (Firefox?)");
    setSttBadge("🎤 STT 미지원", "off");
    return null;
  }
  const r = new SR();
  r.lang = "ko-KR";
  r.continuous = true;
  r.interimResults = true;
  r.onstart = () => {
    console.log("[SR] started");
    setSttBadge("🎤 STT 듣는 중", "on");
  };
  r.onresult = (ev) => {
    // 사용자가 말하기 시작하자마자 interim 을 화면에 흘려준다.
    // (AGENT 발화 중에도 표시 — 에코로 가짜 interim 이 잠깐 떠도 ElevenLabs
    //  의 user_transcript(권위) 가 도착하면 자동 교체/삭제됨)
    let interim = "";
    let finalChunk = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const t = ev.results[i][0].transcript;
      if (ev.results[i].isFinal) finalChunk += t;
      else interim += t;
    }
    const text = (finalChunk + interim).trim();
    if (!text) return;
    if (!interimBubble) {
      interimBubble = addMsg("user", text);
      interimBubble.classList.add("interim");
    } else {
      interimBubble.querySelector(".text").textContent = text;
      logEl.scrollTop = logEl.scrollHeight;
    }
  };
  r.onerror = (e) => {
    console.warn("[SR] error:", e.error || e);
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      setSttBadge("🎤 STT 권한거부", "error");
    } else if (e.error === "no-speech" || e.error === "aborted") {
      // 정상적인 일시 중단 — 그냥 재시작에 맡김
    } else {
      setSttBadge("🎤 STT 오류", "error");
    }
  };
  // continuous=true 라도 무음 후 종료되는 경우가 있어 자동 재시작.
  // 단, AGENT 가 발화 중이면 재시작하지 않는다 (에코 차단).
  // start() 를 stop() 직후 즉시 부르면 InvalidStateError 가 나므로 짧은 지연.
  r.onend = () => {
    console.log("[SR] ended");
    if (recognition !== r) return;
    if (playback.speaking) return;          // AGENT 발화 중엔 보류
    setTimeout(() => {
      if (recognition !== r || playback.speaking) return;
      try { r.start(); } catch (e) { console.warn("[SR] restart failed:", e); }
    }, 150);
  };
  return r;
}

// ---------- Audio capture (AudioWorklet, PCM16 @ 16kHz, 40ms 청크) ----------
const SAMPLE_RATE = 16000;
const CHUNK_SAMPLES = 640;  // 40ms @ 16kHz

const WORKLET_CODE = `
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(0);
    this.target = ${CHUNK_SAMPLES};
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];
    const merged = new Float32Array(this.buffer.length + ch.length);
    merged.set(this.buffer, 0);
    merged.set(ch, this.buffer.length);
    this.buffer = merged;
    while (this.buffer.length >= this.target) {
      const chunk = this.buffer.subarray(0, this.target);
      this.buffer = this.buffer.slice(this.target);
      const pcm = new Int16Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) {
        let s = Math.max(-1, Math.min(1, chunk[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      this.port.postMessage(pcm.buffer);
    }
    return true;
  }
}
registerProcessor("capture", CaptureProcessor);
`;

function arrayBufferToBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

function base64ToArrayBuffer(b64) {
  const bin = atob(b64);
  const buf = new ArrayBuffer(bin.length);
  const view = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) view[i] = bin.charCodeAt(i);
  return buf;
}

function schedulePcm16Playback(b64) {
  if (!audioCtx) return;
  const buf = base64ToArrayBuffer(b64);
  const pcm16 = new Int16Array(buf);
  const f32 = new Float32Array(pcm16.length);
  for (let i = 0; i < pcm16.length; i++) f32[i] = pcm16[i] / 32768;
  const audioBuffer = audioCtx.createBuffer(1, f32.length, SAMPLE_RATE);
  audioBuffer.copyToChannel(f32, 0);
  const src = audioCtx.createBufferSource();
  src.buffer = audioBuffer;
  src.connect(audioCtx.destination);
  const startAt = Math.max(audioCtx.currentTime + 0.02, playback.nextStart);
  src.start(startAt);
  playback.nextStart = startAt + audioBuffer.duration;
  playback.sources.push(src);
  // 첫 청크가 큐에 들어오면 AGENT 발화 시작 → mic 차단
  playback.setSpeaking(true);
  src.onended = () => {
    const i = playback.sources.indexOf(src);
    if (i >= 0) playback.sources.splice(i, 1);
    // 마지막 청크가 끝나면 AGENT 발화 종료 → mic 재개
    if (playback.sources.length === 0) playback.setSpeaking(false);
  };
}

// ---------- Start / Stop ----------
async function start() {
  startBtn.disabled = true;
  setStatus("연결 중...", "");

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (e) {
    setStatus("마이크 권한 거부됨", "error");
    startBtn.disabled = false;
    return;
  }

  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
  if (audioCtx.state === "suspended") await audioCtx.resume();

  const blob = new Blob([WORKLET_CODE], { type: "application/javascript" });
  await audioCtx.audioWorklet.addModule(URL.createObjectURL(blob));
  micSource = audioCtx.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioCtx, "capture");
  workletNode.port.onmessage = (ev) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    // 에코 차단: AGENT 발화 중엔 mic 송신 자체를 막음 (half-duplex)
    if (agentIsSpeaking()) {
      if (ECHO_HARD_MUTE) return;
      // 끼어들기 모드: 충분히 큰 음성만 통과
      if (rmsEnergyInt16(ev.data) < ECHO_INTERRUPT_THRESHOLD) return;
    }
    ws.send(JSON.stringify({ type: "audio", audio: arrayBufferToBase64(ev.data) }));
  };
  micSource.connect(workletNode);
  // worklet 은 destination 에 연결하지 않음 (마이크 입력을 스피커로 다시 내보내지 않기 위해)

  // 브라우저 STT 시작 — 사용자 발화를 즉시 화면에 표시 (ElevenLabs 권위값으로 후속 교체)
  recognition = setupRecognition();
  if (recognition) {
    try { recognition.start(); }
    catch (e) {
      console.warn("[SR] initial start failed:", e);
      setSttBadge("🎤 STT 시작실패", "error");
    }
  }

  const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${wsProto}//${location.host}/ws`);
  ws.onopen = () => {
    setStatus("🟢 통화 중", "live");
    stopBtn.disabled = false;
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    switch (msg.type) {
      case "audio_delta":
        schedulePcm16Playback(msg.audio);
        break;
      case "agent_text_done": {
        const text = msg.transcript || "";
        if (agentPlaceholder) {
          // setSpeaking(true) 시점에 만들어 둔 빈 자리에 채워 넣어
          // RIDER 메시지와의 화면 순서를 보존
          agentPlaceholder.querySelector(".text").textContent = text;
          agentPlaceholder.classList.remove("pending");
          agentPlaceholder = null;
        } else {
          addMsg("agent", text);
        }
        break;
      }
      case "user_text_done": {
        // ElevenLabs 의 권위 transcript 도착 — 브라우저 interim 버블이 있으면
        // 그 자리에 정식 텍스트로 교체, 없으면 새 버블 추가.
        const finalText = (msg.transcript || "").trim();
        if (interimBubble) {
          if (finalText) {
            interimBubble.querySelector(".text").textContent = finalText;
            interimBubble.classList.remove("interim");
          } else {
            interimBubble.remove();
          }
          interimBubble = null;
        } else if (finalText) {
          addMsg("user", finalText);
        }
        break;
      }
      case "interrupt":
        playback.reset();
        break;
      case "error":
        setStatus("오류: " + msg.message, "error");
        break;
    }
  };
  ws.onclose = () => {
    setStatus("연결 종료", "");
    stop(true);
  };
  ws.onerror = () => {
    setStatus("WebSocket 오류", "error");
  };
}

function stop(fromServer) {
  stopBtn.disabled = true;
  startBtn.disabled = false;
  if (ws && ws.readyState === WebSocket.OPEN && !fromServer) {
    try { ws.send(JSON.stringify({ type: "stop" })); } catch(_) {}
    try { ws.close(); } catch(_) {}
  }
  ws = null;
  if (workletNode) { try { workletNode.disconnect(); } catch(_) {} workletNode = null; }
  if (micSource)   { try { micSource.disconnect();   } catch(_) {} micSource = null; }
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
  if (audioCtx)    { try { audioCtx.close(); } catch(_) {} audioCtx = null; }
  if (recognition) {
    const r = recognition;
    recognition = null;        // onend 자동 재시작 방지
    try { r.stop(); } catch(_) {}
  }
  interimBubble = null;
  agentPlaceholder = null;
  playback.reset();
  setSttBadge("🎤 STT 대기", "");
  if (!fromServer) setStatus("대기 중", "");
}

startBtn.addEventListener("click", start);
stopBtn.addEventListener("click", () => stop(false));
window.addEventListener("beforeunload", () => stop(false));
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "7862"))  # Railway 는 PORT 주입, 로컬은 7862 유지
    uvicorn.run(app, host="0.0.0.0", port=port)
