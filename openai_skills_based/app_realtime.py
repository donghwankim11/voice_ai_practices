"""배달의민족 고객센터 AGENT — Realtime API + LangChain Skills 패턴.

원본 ../openai_based/app_realtime.py 의 양방향 스트리밍/서버 VAD 구조에
LangChain Skills 가이드(https://wikidocs.net/318950)의 *패턴* 을 결합한다:

    1. 시나리오별 프롬프트를 skills/*.md 의 description + content 로 분리
    2. 시스템 프롬프트에는 *description 목록* 만 노출 (경량 / 점진적 공개)
    3. AGENT 가 통화 시작 직전에 load_skill 함수콜로 *content* 를 받아옴
    4. 시나리오가 늘어도 BASE_INSTRUCTIONS 토큰량은 일정

주: LangChain 가이드의 create_agent + AgentMiddleware 는 동기 호출 모델이라
    Realtime API 양방향 스트림과 매끄럽게 결합되지 않는다. 따라서 langchain
    런타임을 쓰지 않고, Skills *패턴* (Skill 데이터클래스 + load_skill 도구 +
    점진적 공개) 만 Realtime 네이티브 함수콜링으로 옮겨 구현했다.

실행:
    python app_realtime.py
    # → http://localhost:7861

흐름:
    [브라우저] start 메시지 + scenario 선택값 ─▶ [FastAPI]
        ─▶ Realtime 세션 open (instructions = BASE + 스킬 description 목록만)
        ─▶ 시스템 메시지 "이번 시나리오: <name>. load_skill 먼저 호출"
        ─▶ response.create
        ─▶ [AGENT] load_skill(name) 함수콜
        ─▶ [서버] skill content 를 function_call_output 으로 회신 + response.create
        ─▶ [AGENT] 본문의 첫 발화 그대로 통화 시작
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

from skills_registry import SKILLS, load_skill, skill_list_for_prompt

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
# 시스템 프롬프트 — 스킬 description 만 노출 (본문은 load_skill 후 주입됨)
# ---------------------------------------------------------------------------
BASE_INSTRUCTIONS = f"""\
당신은 '배달의민족(우아한형제들)' 고객센터 상담사 'AGENT' 입니다.
지금 배달원(RIDER)에게 전화를 걸어 특정 시나리오를 처리해야 합니다.

[톤/스타일]
- 한국어 존댓말, 정중하고 간결하게.
- 한 응답에 한 가지만 묻고, RIDER 의 답을 들은 뒤 다음 질문으로 넘어갑니다.
- 한국어 고객센터 상담사 톤으로 또렷하고 친절하게, 전화 통화처럼 자연스럽고 약간 빠른 속도로, 또박또박 끊김 없이 말합니다.
- 마무리 후에는 새로운 질문을 만들지 말고 통화를 종료합니다.

[질문 규칙 — 모든 시나리오 공통, 매우 중요]
- 스킬 본문의 [검증해야 할 사실] 각 항목을 *정확히 한 번씩만* 묻습니다.
- 같은 사실을 표현만 바꿔 다시 묻지 마세요. 표현이 살짝 다르더라도 본질이 같은 질문이면 중복으로 간주합니다.
- RIDER 의 한 답변에 여러 사실이 동시에 확인되면, 그 사실들을 모두 검증된 것으로 간주하고 다음 사실로 넘어갑니다.
- 모든 사실이 확인된 직후에는 종합 재확인 질문을 *추가하지 말고* 바로 감사 인사로 통화를 종료합니다.

[출력 형식]
- 출력은 그대로 음성으로 합성됩니다.
- markdown, 글머리표, 이모지, 괄호 부연설명 사용 금지.
- 한 응답은 1~3문장 이내.

[사용 가능한 스킬]
{skill_list_for_prompt()}

[작동 규칙 — 매우 중요]
1. 시스템이 통화 시작 시점에 처리할 시나리오 이름을 알려줍니다.
2. 알려준 시나리오에 해당하는 스킬을 load_skill 도구로 *반드시 먼저* 로드하세요.
3. 로드된 본문에 명시된 [통화 목적], [검증해야 할 사실], [첫 발화] 를 따르세요.
4. 본문의 [첫 발화] 문장 그대로 통화를 시작하세요.
5. 본문에 없는 정보는 임의로 만들지 말고, 필요하면 RIDER 에게 물어 확인하세요.
"""

# ---------------------------------------------------------------------------
# Realtime function tool 정의 — LangChain @tool 의 등가물
# ---------------------------------------------------------------------------
LOAD_SKILL_TOOL = {
    "type": "function",
    "name": "load_skill",
    "description": (
        "주어진 스킬 이름의 상세 본문(통화 목적, 검증 사실, 첫 발화 등)을 로드합니다. "
        "시스템이 알려준 시나리오 이름으로 통화 시작 직전에 반드시 한 번 호출하세요."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "enum": list(SKILLS.keys()),
                "description": "로드할 스킬의 이름",
            }
        },
        "required": ["skill_name"],
    },
}


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
            headers={"WWW-Authenticate": 'Basic realm="baemin-openai-skills"'},
        )


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="Baemin 음성 AGENT (Realtime + Skills)")
app.add_middleware(BasicAuthMiddleware)


@app.get("/")
async def root():
    return HTMLResponse(_render_index_html())


@app.websocket("/ws")
async def ws_endpoint(client_ws: WebSocket):
    """브라우저 ↔ Azure Realtime API 브릿지 + Skills 패턴 함수콜 처리."""
    await client_ws.accept()
    print("[ws] client connected")

    # 1) 첫 메시지로 시나리오를 받는다 (start 신호)
    try:
        first = json.loads(await client_ws.receive_text())
    except Exception as e:
        await _safe_send(client_ws, {"type": "error", "message": f"start payload 파싱 실패: {e}"})
        await client_ws.close()
        return

    if first.get("type") != "start" or first.get("scenario") not in SKILLS:
        await _safe_send(client_ws, {
            "type": "error",
            "message": f"잘못된 start payload: {first!r}. 시나리오: {list(SKILLS.keys())}",
        })
        await client_ws.close()
        return

    scenario = first["scenario"]
    print(f"[ws] scenario = {scenario}")

    try:
        async with async_client.beta.realtime.connect(
            model=REALTIME_DEPLOYMENT,
        ) as oai:
            await oai.session.update(session={
                "modalities": ["text", "audio"],
                "voice": "alloy",
                "instructions": BASE_INSTRUCTIONS,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
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
                "tools": [LOAD_SKILL_TOOL],
                "tool_choice": "auto",
            })

            # 2) 시나리오 지정 시스템 메시지 + 첫 응답 트리거
            #    → AGENT 가 load_skill 함수콜로 시작하도록 유도
            await oai.conversation.item.create(item={
                "type": "message",
                "role": "system",
                "content": [{
                    "type": "input_text",
                    "text": (
                        f"이번 통화에서 처리할 시나리오는 '{scenario}' 입니다. "
                        "load_skill 도구로 해당 스킬 본문을 먼저 로드한 뒤, "
                        "본문에 적힌 [첫 발화] 그대로 통화를 시작하세요."
                    ),
                }],
            })
            await _safe_send(client_ws, {"type": "scenario_set", "scenario": scenario})
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
                """Realtime API → 브라우저 중계 + 함수콜(load_skill) 처리."""
                async for event in oai:
                    etype = event.type

                    if etype == "response.audio.delta":
                        await _safe_send(client_ws, {
                            "type": "audio_delta",
                            "audio": event.delta,
                        })

                    elif etype == "response.audio_transcript.delta":
                        await _safe_send(client_ws, {
                            "type": "agent_text_delta",
                            "delta": event.delta,
                        })

                    elif etype == "response.audio_transcript.done":
                        await _safe_send(client_ws, {
                            "type": "agent_text_done",
                            "transcript": getattr(event, "transcript", ""),
                        })

                    elif etype == "input_audio_buffer.speech_started":
                        await _safe_send(client_ws, {"type": "interrupt"})

                    elif etype == "input_audio_buffer.speech_stopped":
                        await _safe_send(client_ws, {"type": "user_speech_stopped"})

                    elif etype == "conversation.item.input_audio_transcription.delta":
                        await _safe_send(client_ws, {
                            "type": "user_text_delta",
                            "delta": getattr(event, "delta", ""),
                        })

                    elif etype == "conversation.item.input_audio_transcription.completed":
                        await _safe_send(client_ws, {
                            "type": "user_text_done",
                            "transcript": getattr(event, "transcript", ""),
                        })

                    # ─── Skills 패턴의 핵심: load_skill 함수콜 처리 ─────────
                    elif etype == "response.function_call_arguments.done":
                        call_id = event.call_id
                        fname = event.name
                        try:
                            args = json.loads(event.arguments or "{}")
                        except json.JSONDecodeError:
                            args = {}

                        if fname == "load_skill":
                            requested = args.get("skill_name", "")
                            output = load_skill(requested)
                            print(f"[skill] load_skill('{requested}') → {len(output)} chars")
                            await _safe_send(client_ws, {
                                "type": "skill_loaded",
                                "skill": requested,
                            })
                        else:
                            output = f"알 수 없는 함수: {fname}"
                            print(f"[skill] unknown function call: {fname}")

                        # 함수 결과를 모델에 회신 후 응답 재생성
                        await oai.conversation.item.create(item={
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output,
                        })
                        await oai.response.create()
                    # ─────────────────────────────────────────────────────────

                    elif etype == "response.done":
                        await _safe_send(client_ws, {"type": "response_done"})

                    elif etype == "error":
                        err = getattr(event, "error", event)
                        print(f"[oai error] {err}")
                        await _safe_send(client_ws, {
                            "type": "error",
                            "message": str(err),
                        })

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
        await _safe_send(client_ws, {
            "type": "error",
            "message": f"{type(e).__name__}: {e}",
        })


async def _safe_send(ws: WebSocket, payload: dict) -> None:
    """WebSocket 이 이미 끊겼을 때 send 가 던지는 예외를 무시한다."""
    with suppress(Exception):
        await ws.send_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# 브라우저 페이지 (단일 HTML, 시나리오 선택 드롭다운 추가)
# ---------------------------------------------------------------------------
def _render_index_html() -> str:
    options = "\n".join(
        f'<option value="{name}">{name} — {SKILLS[name].description}</option>'
        for name in SKILLS
    )
    return INDEX_HTML_TEMPLATE.replace("__SCENARIO_OPTIONS__", options)


INDEX_HTML_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>📞 Baemin 음성 AGENT (Realtime + Skills)</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 760px; margin: 32px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 22px; margin-bottom: 8px; }
  .desc { color: #555; line-height: 1.55; font-size: 14px; }
  .scenario-row { margin: 14px 0 4px; display: flex; gap: 8px; align-items: center; }
  .scenario-row label { font-size: 13px; color: #374151; }
  select { padding: 8px 10px; font-size: 14px; border: 1px solid #ccc;
           border-radius: 6px; min-width: 360px; background: #fff; }
  .controls { margin: 12px 0; display: flex; gap: 8px; align-items: center; }
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
  .msg.agent  { background: #eff6ff; }
  .msg.agent .role  { color: #1d4ed8; }
  .msg.user   { background: #fef9c3; }
  .msg.user .role   { color: #854d0e; }
  .msg.system { background: #f3f4f6; color: #4b5563; font-style: italic; }
  .msg.system .role { color: #6b7280; }
  .hint { color: #6b7280; font-size: 12px; margin-top: 8px; }
</style>
</head>
<body>
<h1>📞 배달의민족 고객센터 음성 AGENT — Realtime + Skills</h1>
<p class="desc">
  AI 상담사(AGENT)가 배달원(RIDER)에게 전화를 걸어 시나리오별 통화를 진행합니다.<br/>
  시나리오를 선택하고 [통화 시작]을 누르면, AGENT 가 먼저 <b>load_skill</b> 로
  해당 시나리오 본문을 점진적 공개(progressive disclosure) 방식으로 로드한 뒤
  통화를 시작합니다. 발화 도중에 끼어들어 말해도 됩니다 (서버 VAD 자동 인터럽션).
</p>

<div class="scenario-row">
  <label for="scenario">시나리오</label>
  <select id="scenario">
__SCENARIO_OPTIONS__
  </select>
</div>

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

const playback = {
  sources: [],
  nextStart: 0,
  reset() {
    for (const s of this.sources) { try { s.stop(); } catch(_){} }
    this.sources = [];
    this.nextStart = 0;
  },
};

let agentBuffer = "";
let agentLogEl = null;
let userBuffer  = "";
let userLogEl   = null;

const startBtn   = document.getElementById("startBtn");
const stopBtn    = document.getElementById("stopBtn");
const statusEl   = document.getElementById("status");
const logEl      = document.getElementById("log");
const scenarioEl = document.getElementById("scenario");

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}

function addMsg(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  const label = role === "agent" ? "AGENT" : (role === "user" ? "RIDER" : "SYSTEM");
  div.innerHTML = `<div class="role">${label}</div><div class="text"></div>`;
  div.querySelector(".text").textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}

function ensureAgentLog() {
  if (!agentLogEl) agentLogEl = addMsg("agent", "");
  return agentLogEl;
}
function flushAgentLog(finalText) {
  if (finalText !== undefined) ensureAgentLog().querySelector(".text").textContent = finalText;
  agentLogEl = null;
  agentBuffer = "";
}
function ensureUserLog() {
  if (!userLogEl) userLogEl = addMsg("user", "");
  return userLogEl;
}
function flushUserLog(finalText) {
  if (finalText !== undefined) {
    const text = (finalText || "").trim();
    if (!text) {
      if (userLogEl) userLogEl.remove();
    } else {
      ensureUserLog().querySelector(".text").textContent = text;
    }
  }
  userLogEl = null;
  userBuffer = "";
}

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

async function start() {
  startBtn.disabled = true;
  scenarioEl.disabled = true;
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
    scenarioEl.disabled = false;
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

  const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${wsProto}//${location.host}/ws`);
  ws.onopen = () => {
    // 첫 메시지로 시나리오 전달 (서버는 이걸 받기 전엔 세션을 안 연다)
    ws.send(JSON.stringify({ type: "start", scenario: scenarioEl.value }));
    setStatus("🟢 통화 중", "live");
    stopBtn.disabled = false;
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    switch (msg.type) {
      case "scenario_set":
        addMsg("system", `시나리오 지정: ${msg.scenario} (AGENT 가 load_skill 호출 예정)`);
        break;
      case "skill_loaded":
        addMsg("system", `🧩 스킬 로드 완료: ${msg.skill}`);
        break;
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
        if (userLogEl && !userBuffer) {
          userLogEl.querySelector(".text").textContent = "(전사 중…)";
        }
        break;
      case "response_done":
        break;
      case "error":
        setStatus("오류: " + msg.message, "error");
        break;
    }
  };
  ws.onclose = () => { setStatus("연결 종료", ""); stop(true); };
  ws.onerror = () => { setStatus("WebSocket 오류", "error"); };
}

function stop(fromServer) {
  stopBtn.disabled = true;
  startBtn.disabled = false;
  scenarioEl.disabled = false;
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
