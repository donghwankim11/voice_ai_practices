"""배달의민족 고객센터 AGENT ↔ RIDER 실시간 음성 통화 (Realtime API + Server VAD).

`app.py` 가 턴 기반(녹음→전송→응답 재생)이라면, 본 파일은 양방향 스트리밍이다.
서버사이드 VAD 가 RIDER 의 발화 시작/끝을 자동 감지하고, AGENT 발화 도중에
RIDER 가 끼어들면 자동으로 AGENT 발화를 끊고(barge-in) 답변을 처리한다.

실행:
    python app_realtime.py
    # → http://localhost:7861

흐름:
    [브라우저 마이크 PCM16 24kHz] ──WS──▶ [FastAPI 서버] ──async WS──▶ [Azure Realtime API]
                                                                          │
    [브라우저 스피커 재생] ◀──WS── [FastAPI 서버] ◀──async WS── [response.audio.delta]

VAD/인터럽션:
    Realtime 세션을 server_vad + interrupt_response=True 로 설정해두면,
    1) RIDER 가 말을 시작하면 서버가 input_audio_buffer.speech_started 발사
    2) AGENT 발화 도중이면 자동으로 response.cancel + 새 turn 처리
    3) 본 서버는 speech_started 를 받자마자 클라이언트에 'interrupt' 신호를 보내
       이미 큐잉된 AGENT 오디오 재생을 즉시 중단시킨다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
from contextlib import suppress

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from openai import AsyncAzureOpenAI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Azure OpenAI Realtime 클라이언트
# ---------------------------------------------------------------------------
load_dotenv()

REALTIME_DEPLOYMENT = "gpt-realtime-mini"

async_client = AsyncAzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version="2025-04-01-preview",
)

# ---------------------------------------------------------------------------
# 시나리오 — AGENT 의 역할/목표/톤 + 시작 발화 지시
# (app.py 의 SCENARIO_SYSTEM_PROMPT 와 동일 컨셉, 첫 발화 지시 1줄 추가)
# ---------------------------------------------------------------------------
OPENING_LINE = (
    "안녕하세요, 배달의민족 고객센터입니다. "
    "주문 건 배달 주소 확인차 연락드렸습니다. 잠시 통화 가능하실까요?"
)

SCENARIO_INSTRUCTIONS = f"""\
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
- 한국어 고객센터 상담사 톤으로 또렷하고 친절하게, 전화 통화처럼 자연스럽고 약간 빠른 속도로, 또박또박 끊김 없이 말합니다.

[출력 형식]
- 출력은 그대로 음성으로 합성됩니다.
- markdown, 글머리표, 이모지, 괄호 부연설명 사용 금지.
- 한 응답은 1~3문장 이내.

[대화 시작]
RIDER 가 응답하기 전에, 당신이 먼저 정확히 다음 문장으로 통화를 시작하세요:
"{OPENING_LINE}"
"""


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
            headers={"WWW-Authenticate": 'Basic realm="baemin-openai"'},
        )


# ---------------------------------------------------------------------------
# FastAPI 앱
# ---------------------------------------------------------------------------
app = FastAPI(title="Baemin 음성 AGENT (Realtime + VAD)")
app.add_middleware(BasicAuthMiddleware)


@app.get("/")
async def root():
    return HTMLResponse(INDEX_HTML)


@app.websocket("/ws")
async def ws_endpoint(client_ws: WebSocket):
    """브라우저 ↔ Azure Realtime API 브릿지."""
    await client_ws.accept()
    print("[ws] client connected")

    try:
        async with async_client.beta.realtime.connect(
            model=REALTIME_DEPLOYMENT,
        ) as oai:
            await oai.session.update(session={
                "modalities": ["text", "audio"],
                "voice": "alloy",
                "instructions": SCENARIO_INSTRUCTIONS,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                # RIDER 발화도 화면에 텍스트로 표시하기 위해 입력 음성 전사를 켠다.
                # (Azure 리소스에 gpt-4o-mini-transcribe deployment 가 존재해야 함)
                "input_audio_transcription": {
                    "model": "gpt-4o-mini-transcribe",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                    "create_response": True,
                    "interrupt_response": True,
                },
            })

            # AGENT 가 먼저 시작 발화를 하도록 즉시 응답 생성 트리거
            await oai.response.create()

            async def from_client():
                """브라우저 → Realtime API 마이크 PCM 전달."""
                while True:
                    raw = await client_ws.receive_text()
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    mtype = msg.get("type")
                    if mtype == "audio":
                        await oai.input_audio_buffer.append(audio=msg["audio"])
                    elif mtype == "stop":
                        return

            async def from_openai():
                """Realtime API → 브라우저 오디오/텍스트/이벤트 중계."""
                async for event in oai:
                    etype = event.type

                    if etype == "response.audio.delta":
                        await client_ws.send_text(json.dumps({
                            "type": "audio_delta",
                            "audio": event.delta,
                        }))

                    elif etype == "response.audio_transcript.delta":
                        await client_ws.send_text(json.dumps({
                            "type": "agent_text_delta",
                            "delta": event.delta,
                        }))

                    elif etype == "response.audio_transcript.done":
                        await client_ws.send_text(json.dumps({
                            "type": "agent_text_done",
                            "transcript": getattr(event, "transcript", ""),
                        }))

                    elif etype == "input_audio_buffer.speech_started":
                        # RIDER 가 말을 시작 → 클라이언트 재생 큐 즉시 비우기
                        await client_ws.send_text(json.dumps({"type": "interrupt"}))

                    elif etype == "input_audio_buffer.speech_stopped":
                        await client_ws.send_text(json.dumps({"type": "user_speech_stopped"}))

                    elif etype == "conversation.item.input_audio_transcription.delta":
                        # 일부 모델/버전이 부분 전사 delta 를 흘려준다 (없을 수도 있음)
                        await client_ws.send_text(json.dumps({
                            "type": "user_text_delta",
                            "delta": getattr(event, "delta", ""),
                        }))

                    elif etype == "conversation.item.input_audio_transcription.completed":
                        await client_ws.send_text(json.dumps({
                            "type": "user_text_done",
                            "transcript": getattr(event, "transcript", ""),
                        }))

                    elif etype == "response.done":
                        await client_ws.send_text(json.dumps({"type": "response_done"}))

                    elif etype == "error":
                        err = getattr(event, "error", event)
                        print(f"[oai error] {err}")
                        await client_ws.send_text(json.dumps({
                            "type": "error",
                            "message": str(err),
                        }))

            client_task = asyncio.create_task(from_client())
            oai_task = asyncio.create_task(from_openai())

            done, pending = await asyncio.wait(
                {client_task, oai_task},
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
# ---------------------------------------------------------------------------
INDEX_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>📞 Baemin 음성 AGENT (Realtime + VAD)</title>
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
  #log { margin-top: 16px; padding: 12px; background: #f9fafb;
         border: 1px solid #e5e7eb; border-radius: 8px;
         max-height: 460px; overflow-y: auto; }
  .msg { margin: 6px 0; padding: 8px 12px; border-radius: 8px;
         font-size: 14px; line-height: 1.5; }
  .msg .role { font-size: 11px; font-weight: 600; color: #6b7280;
               margin-bottom: 2px; letter-spacing: 0.04em; }
  .msg.agent { background: #eff6ff; }
  .msg.agent .role { color: #1d4ed8; }
  .msg.user  { background: #fef9c3; }
  .msg.user .role { color: #854d0e; }
  .hint { color: #6b7280; font-size: 12px; margin-top: 8px; }
</style>
</head>
<body>
<h1>📞 배달의민족 고객센터 음성 AGENT — 실시간(VAD) 버전</h1>
<p class="desc">
  AI 상담사(AGENT)가 배달원(RIDER)에게 전화를 걸어,
  배달 주소 변경 건(102동 → 112동)의 정상 배달 여부를 확인합니다.<br/>
  버튼을 누르면 AGENT 가 먼저 발화하며, <b>발화 도중에 끼어들어 말해도 됩니다</b> (서버 VAD 가 자동으로 끊습니다).
</p>

<div class="controls">
  <button id="startBtn" class="primary">📞 통화 시작</button>
  <button id="stopBtn" disabled>⏹ 통화 종료</button>
  <span id="status">대기 중</span>
</div>
<div class="hint">처음 접속 시 브라우저가 마이크 권한을 묻습니다. 허용해 주세요.</div>

<div id="log"></div>

<script>
let ws = null;
let audioCtx = null;
let mediaStream = null;
let micSource = null;
let workletNode = null;

// 재생 큐: 모든 BufferSourceNode 를 보관 → interrupt 시 일괄 stop
const playback = {
  sources: [],
  nextStart: 0,
  reset() {
    for (const s of this.sources) { try { s.stop(); } catch(_){} }
    this.sources = [];
    this.nextStart = 0;
  },
};

// 진행 중인 발화 텍스트(부분) — 역할별로 따로 관리
let agentBuffer = "";
let agentLogEl = null;
let userBuffer  = "";
let userLogEl   = null;

const startBtn = document.getElementById("startBtn");
const stopBtn  = document.getElementById("stopBtn");
const statusEl = document.getElementById("status");
const logEl    = document.getElementById("log");

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
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

function ensureAgentLog() {
  if (!agentLogEl) {
    agentLogEl = addMsg("agent", "");
  }
  return agentLogEl;
}

function flushAgentLog(finalText) {
  if (finalText !== undefined) {
    ensureAgentLog().querySelector(".text").textContent = finalText;
  }
  agentLogEl = null;
  agentBuffer = "";
}

function ensureUserLog() {
  if (!userLogEl) {
    userLogEl = addMsg("user", "");
  }
  return userLogEl;
}

function flushUserLog(finalText) {
  if (finalText !== undefined) {
    const text = (finalText || "").trim();
    if (!text) {
      // 빈 전사면 미리 만들어둔 빈 말풍선 제거
      if (userLogEl) userLogEl.remove();
    } else {
      ensureUserLog().querySelector(".text").textContent = text;
    }
  }
  userLogEl = null;
  userBuffer = "";
}

// ---------- Audio capture (AudioWorklet, PCM16 @ 24kHz, 40ms 청크) ----------
const WORKLET_CODE = `
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(0);
    this.target = 960; // 40ms @ 24kHz
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];
    // append
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
  const audioBuffer = audioCtx.createBuffer(1, f32.length, 24000);
  audioBuffer.copyToChannel(f32, 0);
  const src = audioCtx.createBufferSource();
  src.buffer = audioBuffer;
  src.connect(audioCtx.destination);
  const startAt = Math.max(audioCtx.currentTime + 0.02, playback.nextStart);
  src.start(startAt);
  playback.nextStart = startAt + audioBuffer.duration;
  playback.sources.push(src);
  src.onended = () => {
    const i = playback.sources.indexOf(src);
    if (i >= 0) playback.sources.splice(i, 1);
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

  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
  if (audioCtx.state === "suspended") await audioCtx.resume();

  const blob = new Blob([WORKLET_CODE], { type: "application/javascript" });
  await audioCtx.audioWorklet.addModule(URL.createObjectURL(blob));
  micSource = audioCtx.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioCtx, "capture");
  workletNode.port.onmessage = (ev) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "audio", audio: arrayBufferToBase64(ev.data) }));
  };
  micSource.connect(workletNode);
  // worklet 은 destination 에 연결하지 않음 (마이크 입력을 스피커로 다시 내보내지 않기 위해)

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
      case "agent_text_delta":
        agentBuffer += msg.delta;
        ensureAgentLog().querySelector(".text").textContent = agentBuffer;
        logEl.scrollTop = logEl.scrollHeight;
        break;
      case "agent_text_done":
        flushAgentLog(msg.transcript || agentBuffer);
        break;
      case "interrupt":
        playback.reset();
        if (agentBuffer) flushAgentLog(agentBuffer);
        // RIDER 가 말을 시작했음 → 사용자 말풍선을 미리 띄워 "..." 로 표시
        ensureUserLog().querySelector(".text").textContent = "…";
        logEl.scrollTop = logEl.scrollHeight;
        break;
      case "user_text_delta":
        userBuffer += msg.delta || "";
        ensureUserLog().querySelector(".text").textContent = userBuffer;
        logEl.scrollTop = logEl.scrollHeight;
        break;
      case "user_text_done":
        flushUserLog(msg.transcript || userBuffer);
        break;
      case "user_speech_stopped":
        // 발화는 끝났지만 전사 결과는 아직 — 인디케이터 갱신
        if (userLogEl && !userBuffer) {
          userLogEl.querySelector(".text").textContent = "(전사 중…)";
        }
        break;
      case "response_done":
        // 응답 종료 — 다음 turn 대기
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
  playback.reset();
  flushAgentLog();
  flushUserLog();
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

    port = int(os.getenv("PORT", "7861"))  # Railway 는 PORT 주입, 로컬은 7861 유지
    uvicorn.run(app, host="0.0.0.0", port=port)
