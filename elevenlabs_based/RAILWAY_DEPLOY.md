# Railway 배포 가이드 — `app_realtime.py` (ElevenLabs)

`elevenlabs_based/app_realtime.py` (FastAPI + WebSocket + ElevenLabs Convai) 를 Railway 에 올려
`https://<your-app>.up.railway.app` 으로 외부에서 접속하기 위한 절차.

> 같은 구조의 Azure OpenAI 버전 가이드 → `openai_skills_based/RAILWAY_DEPLOY.md`

Railway 가 적합한 이유:
- WebSocket 정상 지원 (Convai 양방향 스트리밍 OK)
- HTTPS 자동 부여 → 브라우저 마이크(`getUserMedia`) 동작
- 환경변수로 ElevenLabs 키 / agent_id 안전 보관
- GitHub 푸시 → 자동 빌드/배포

## 0. 사전 준비

- Railway 계정: <https://railway.app> (GitHub 로그인)
- 이 디렉터리가 GitHub 리포에 올라가 있어야 함 → 본 리포: `donghwankim11/voice_ai_practices`
- 로컬에서 `python app_realtime.py` 가 정상 동작하는 상태 (포트 7862)
- `python create_agent.py` 를 한 번 실행해 ElevenLabs agent 가 발급된 상태
  (콘솔에 출력된 `ELEVENLABS_AGENT_ID` 를 Railway Variables 에 넣을 것이므로 별도 기록)

---

## 1. 코드 수정 — 포트는 `$PORT` 에서 받기 (필수)

Railway 는 컨테이너에 임의 포트를 `$PORT` 환경변수로 주입한다. 7862 하드코딩이면 외부 접근이 안 됨.

`app_realtime.py` 마지막 블록 (line 697~700) 을 다음으로 교체:

```python
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "7862"))  # Railway 는 PORT 주입, 로컬은 7862 유지
    uvicorn.run(app, host="0.0.0.0", port=port)
```

> 로컬 개발은 그대로 7862 로 동작, Railway 에서는 자동으로 주입된 포트를 사용.

---

## 2. 시작 명령 정의 — `Procfile` 추가

`elevenlabs_based/Procfile` 생성:

```
web: python app_realtime.py
```

Railway 는 `Procfile` 의 `web:` 명령을 자동 실행한다. Nixpacks 가 `requirements.txt` 를 인식해
의존성 설치까지 처리.

---

## 3. `.gitignore` 점검 — `.env` 절대 커밋 금지

리포 루트 `.gitignore` 에 다음 포함 확인 (이미 설정됨):

```
.env
__pycache__/
```

`.env` 가 이미 커밋된 상태라면 즉시 git 히스토리에서 제거하고 ElevenLabs API 키를 재발급할 것.

---

## 4. (강력 권장) 간단한 접근 보호 — HTTP Basic Auth

Railway 의 공개 URL 은 **알면 누구나 접속 가능**. ElevenLabs 크레딧을 외부인이 갉아먹지 않게
최소한 Basic Auth 게이트를 두자. WebSocket 까지 같이 보호되는 미들웨어 형태:

`app_realtime.py` 상단(임포트 직후) 에 추가:

```python
import base64
import secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

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
```

그리고 `app = FastAPI(...)` 다음 줄에:

```python
app.add_middleware(BasicAuthMiddleware)
```

WebSocket 도 같은 origin 의 Basic Auth 자격증명을 자동으로 실어 보내므로 보호된다.
브라우저는 한 번 로그인하면 세션 동안 자격증명을 캐시.

> 로컬에서는 `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` 를 안 정하면 자동으로 비활성화되어
> 평소처럼 띄울 수 있다.

---

## 5. GitHub 에 푸시

```bash
cd ~/voice_ai_practices
git add elevenlabs_based/app_realtime.py elevenlabs_based/Procfile elevenlabs_based/RAILWAY_DEPLOY.md
git commit -m "Railway 배포 준비(ElevenLabs): PORT 환경변수, Procfile, Basic Auth"
git push
```

---

## 6. Railway 프로젝트 생성 & GitHub 연결

1. <https://railway.app/new> → **Deploy from GitHub repo**
2. `donghwankim11/voice_ai_practices` 선택 → 권한 부여
3. **모노리포(`elevenlabs_based` 외에 다른 폴더 존재)** 이므로:
   - 프로젝트 생성 후 **Settings → Service → Root Directory** 를 `elevenlabs_based` 로 지정
   - **Build → Builder** 는 `Nixpacks` (기본값)
   - **Deploy → Start Command** 가 비어 있으면 `python app_realtime.py` 입력 (Procfile 있으면 자동)

---

## 7. 환경변수 설정 — **반드시 dashboard 에서**

Railway 프로젝트 → **Variables** 탭에서 추가:

| Key | Value |
|---|---|
| `ELEVENLABS_API_KEY` | (실제 API 키) |
| `ELEVENLABS_AGENT_ID` | `create_agent.py` 가 발급한 agent id |
| `BASIC_AUTH_USER` | 원하는 ID (예: `agent`) |
| `BASIC_AUTH_PASS` | 길고 강한 패스워드 |

> Railway 의 Variables 는 빌드/런타임에 환경변수로 주입되며 코드/이미지에는 박히지 않는다.
> `.env` 파일은 절대 커밋하지 말 것.

저장하면 자동 재배포된다.

---

## 8. 공개 URL 발급

**Settings → Networking → Generate Domain** 클릭.
`https://<random>.up.railway.app` 형식의 HTTPS 도메인이 나온다.

브라우저로 접속 → Basic Auth 로그인 창 → 통과하면 마이크 권한 → 통화 시작.

---

## 9. 동작 점검 체크리스트

- [ ] `https://<...>.up.railway.app` 접속 시 401 → 로그인창 → 정상 통과
- [ ] 인덱스 페이지 로드, "📞 통화 시작" 버튼 표시
- [ ] 통화 시작 시 마이크 권한 팝업
- [ ] 브라우저 개발자도구 Network 탭에서 `wss://` 로 WebSocket 연결됨 (101 Switching Protocols)
- [ ] AGENT 첫 발화(OPENING_LINE) 가 음성으로 재생됨
- [ ] 끼어들기(인터럽트) 동작
- [ ] Railway → **Deployments → View Logs** 에 `[ws] client connected`, `[eleven] session started` 출력

---

## 10. (옵션) 커스텀 도메인 + Cloudflare Access 이중 보호

Basic Auth 만으로 부족하다면, Railway 도메인 위에 Cloudflare 를 한 겹 더 씌울 수 있다.

1. Railway → **Settings → Networking → Custom Domain** 에 `agent.example.com` 추가
2. 안내된 CNAME 을 Cloudflare DNS 에 등록 (proxy ON 가능)
3. Cloudflare Zero Trust → Access → Applications 에서 `agent.example.com` 에
   이메일 OTP 또는 SSO 정책 부여

---

## 11. 운영 팁 / 주의사항

- **콜드 스타트**: Railway 의 Free/Hobby 플랜은 일정 시간 미사용 시 슬립할 수 있음.
  접속 시 첫 요청이 5~10초 늦어질 수 있다.
- **로그**: `print(...)` 출력은 Railway Logs 에서 실시간 확인 가능. 전사문 등 민감 정보가
  로그에 흘러가지 않도록 점검 권장.
- **ElevenLabs 크레딧**: Convai 호출 비용은 ElevenLabs 측에서 발생. Basic Auth 비번을 강하게.
- **사내 프록시 SSL**: 코드의 `truststore.SSLContext(...)` 는 macOS 키체인을 신뢰하는 설정이라
  Railway(리눅스) 환경에선 그냥 시스템 기본 CA 만 사용된다 → 별도 변경 불요.
- **재배포**: GitHub `main` 에 푸시하면 자동 재빌드. 수동 재배포는 **Deployments → Redeploy**.

---

## 12. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| 502 Bad Gateway | 앱이 `$PORT` 가 아닌 7862 만 듣고 있음 → 1번 단계 수정 누락 |
| 페이지는 뜨는데 마이크 막힘 | HTTPS 에서만 마이크 허용 → `http://` 가 아니라 `https://` 로 접속 |
| WebSocket `wss://` 연결 실패 | `$PORT` 문제거나, Basic Auth 미들웨어가 WS 도 401 반환 → 페이지 먼저 로그인 후 WS 연결되어야 함 |
| 401 무한 루프 | `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` 오타 또는 양 끝 공백 확인 |
| `ELEVENLABS_AGENT_ID 가 필요합니다` 에러 | Variables 에 `ELEVENLABS_AGENT_ID` 누락 — `create_agent.py` 로 발급 후 등록 |
| `ELEVENLABS_API_KEY 가 필요합니다` 에러 | Variables 저장 후 자동 재배포가 끝났는지 확인. 안 됐으면 수동 Redeploy |
| build 시 `truststore` 관련 오류 | 거의 발생 안 함. 발생 시 Linux 환경 변경에 따른 일시적 이슈 → 재배포로 해결 |

---

## 부록: 변경 파일 요약

- `elevenlabs_based/app_realtime.py` — `$PORT` 처리 + (옵션) `BasicAuthMiddleware`
- `elevenlabs_based/Procfile` — `web: python app_realtime.py`
- `elevenlabs_based/RAILWAY_DEPLOY.md` — 본 문서
- (루트) `.gitignore` — `.env` 보호 (이미 적용)
