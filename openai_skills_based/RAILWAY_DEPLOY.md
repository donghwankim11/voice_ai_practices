# Railway 배포 가이드 — `app_realtime.py`

`app_realtime.py` (FastAPI + WebSocket + Azure OpenAI Realtime) 를 Railway 에 올려
`https://<your-app>.up.railway.app` 으로 외부에서 접속하기 위한 절차.

Railway 가 적합한 이유:
- WebSocket 정상 지원 (Realtime API 양방향 스트리밍 OK)
- HTTPS 자동 부여 → 브라우저 마이크(`getUserMedia`) 동작
- 환경변수로 Azure 키 안전 보관 (코드/이미지에 안 박힘)
- GitHub 푸시 → 자동 빌드/배포

## 0. 사전 준비

- Railway 계정: <https://railway.app> (GitHub 로그인)
- 이 프로젝트가 GitHub 리포에 올라가 있어야 함 (Railway 의 GitHub 연동 사용 권장)
- 로컬에서 `python app_realtime.py` 가 정상 동작하는 상태

---

## 1. 코드 수정 — 포트는 `$PORT` 에서 받기 (필수)

Railway 는 컨테이너에 임의 포트를 `$PORT` 환경변수로 주입한다. 7861 하드코딩이면 외부 접근이 안 됨.

`app_realtime.py` line 651~654 를 다음으로 교체:

```python
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "7861"))  # Railway 는 PORT 주입, 로컬은 7861 유지
    uvicorn.run(app, host="0.0.0.0", port=port)
```

> 로컬 개발은 그대로 7861 로 동작, Railway 에서는 자동으로 주입된 포트를 사용.

---

## 2. 시작 명령 정의 — `Procfile` 추가

리포 루트(또는 이 앱 디렉터리)에 `Procfile` 파일 생성:

```
web: python app_realtime.py
```

Railway 는 `Procfile` 의 `web:` 명령을 자동 실행한다. (Nixpacks 가 `requirements.txt` 를 인식해 의존성 설치까지 처리.)

---

## 3. `.gitignore` 점검 — `.env` 절대 커밋 금지

리포 루트 `.gitignore` 에 다음 포함 확인:

```
.env
.env.*
__pycache__/
```

`.env` 가 이미 커밋된 상태라면 즉시 git 히스토리에서 제거하고 Azure 키를 재발급할 것.

---

## 4. (옵션) `.env.example` 추가

협업·문서화용. 비밀값은 비워두고 키 이름만:

```dotenv
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
```

---

## 5. (강력 권장) 간단한 접근 보호 — HTTP Basic Auth

Railway 의 공개 URL 은 **알면 누구나 접속 가능**. Azure 토큰을 외부인이 갉아먹지 않게
최소한 Basic Auth 게이트를 두자. WebSocket 까지 같이 보호되는 미들웨어 형태:

`app_realtime.py` 상단(임포트 직후, `app = FastAPI(...)` 바로 위 또는 직후) 에 추가:

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
            headers={"WWW-Authenticate": 'Basic realm="baemin-agent"'},
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

## 6. GitHub 에 푸시

```bash
git add app_realtime.py Procfile RAILWAY_DEPLOY.md
git commit -m "Railway 배포 준비: PORT 환경변수, Procfile, Basic Auth"
git push
```

---

## 7. Railway 프로젝트 생성 & GitHub 연결

1. <https://railway.app/new> → **Deploy from GitHub repo** 선택
2. `donghwankim11/voice_ai_practices` 선택 → 권한 부여
3. **모노리포(`openai_skills_based` 외에 다른 폴더 존재)** 이므로 **Root Directory 를 반드시 지정**해야 함.
   설정 안 하면 첫 빌드에서 다음 에러로 실패:

   ```
   ⚠ Script start.sh not found
   ✖ Railpack could not determine how to build the app.

   The app contents that Railpack analyzed contains:
   ./
   ├── openai_skills_based/
   ├── openai_based/
   ├── elevenlabs_based/
   └── .gitignore
   ```

   (Railway 가 리포 루트만 보기 때문에 Python 파일을 못 찾음.)

### 7-1. Root Directory 지정 방법

Railway UI 가 자주 바뀌어서 메뉴 경로가 헷갈릴 수 있다. 두 가지 방법 중 편한 쪽:

**(A) Settings 메뉴에서 직접 지정**
- `Settings → Service → Root Directory` (또는 `Settings → Source → Root Directory`,
  버전에 따라 위치가 다름) 에 `openai_skills_based` 입력 후 저장.

**(B) Railway 우측 Agent 챗봇으로 자연어 지정 (UI 에서 못 찾을 때 — 실제 검증됨)**
- 프로젝트 화면 우측의 Railway AI Agent 패널에 다음과 같이 입력:
  > Set the root directory to `openai_skills_based`

  Agent 가 자동으로 설정을 변경하고 재배포를 트리거한다.

### 7-2. (옵션) 추가 설정

- **Build → Builder**: Railway 는 현재 `Railpack` 이 기본 (구 Nixpacks). `requirements.txt`
  존재만으로 Python 으로 자동 인식되므로 별도 지정 불요.
- **Deploy → Start Command**: `Procfile` 의 `web: python app_realtime.py` 가 자동 인식되므로
  비워둬도 됨. (안 잡힐 때만 `python app_realtime.py` 수동 입력)

---

## 8. 환경변수 설정 — **반드시 dashboard 에서**

Railway 프로젝트 → **Variables** 탭에서 추가:

| Key | Value |
|---|---|
| `AZURE_OPENAI_API_KEY` | (실제 키) |
| `AZURE_OPENAI_ENDPOINT` | `https://<your-resource>.openai.azure.com/` |
| `BASIC_AUTH_USER` | 원하는 ID (예: `agent`) |
| `BASIC_AUTH_PASS` | 길고 강한 패스워드 |

> Railway 의 Variables 는 빌드/런타임에 환경변수로 주입되며 코드/이미지에는 박히지 않는다.
> `.env` 파일은 절대 커밋하지 말 것.

저장하면 자동 재배포된다.

---

## 9. 공개 URL 발급

**Settings → Networking → Generate Domain** 클릭.
`https://<random>.up.railway.app` 형식의 HTTPS 도메인이 나온다.

브라우저로 접속 → Basic Auth 로그인 창 → 통과하면 마이크 권한 → 통화 시작.

---

## 10. 동작 점검 체크리스트

- [ ] `https://<...>.up.railway.app` 접속 시 401 → 로그인창 → 정상 통과
- [ ] 인덱스 페이지 로드, 시나리오 드롭다운 표시
- [ ] 통화 시작 시 마이크 권한 팝업
- [ ] 브라우저 개발자도구 Network 탭에서 `wss://` 로 WebSocket 연결됨 (101 Switching Protocols)
- [ ] AGENT 첫 발화가 음성으로 재생됨
- [ ] 끼어들기(인터럽트) 동작
- [ ] Railway → **Deployments → View Logs** 에서 `[ws] client connected`, `[skill] load_skill(...)` 출력 확인

---

## 11. (옵션) 커스텀 도메인 + Cloudflare Access 이중 보호

Basic Auth 만으로 부족하다면, Railway 도메인 위에 Cloudflare 를 한 겹 더 씌울 수 있다.

1. Railway → **Settings → Networking → Custom Domain** 에 `agent.example.com` 추가
2. 안내된 CNAME 을 Cloudflare DNS 에 등록 (proxy ON 가능)
3. Cloudflare Zero Trust → Access → Applications 에서 `agent.example.com` 에
   이메일 OTP 또는 SSO 정책 부여

이러면 Cloudflare Access 로그인 → Railway → Basic Auth 의 2단계 보호.

---

## 12. 운영 팁 / 주의사항

- **콜드 스타트**: Railway 의 Free/Hobby 플랜은 일정 시간 미사용 시 슬립할 수 있음.
  접속 시 첫 요청이 5~10초 늦어질 수 있다. 상시 응답이 필요하면 유료 플랜.
- **로그**: `print(...)` 출력은 Railway Logs 에서 실시간 확인 가능. 키·전사문 등
  민감 정보가 로그에 흘러가지 않도록 한 번 점검 권장.
- **비용**: Realtime API + Azure 호출 비용은 Azure 측에서 발생. 외부 노출이라
  악용 방지 차원에서 Basic Auth 비번을 강하게.
- **재배포**: GitHub `main` 에 푸시하면 자동 재빌드. 수동 재배포는
  **Deployments → Redeploy**.
- **롤백**: 이전 배포로 되돌리려면 **Deployments** 목록에서 해당 배포의 `⋯` →
  **Redeploy** (또는 git 으로 revert 후 푸시).

---

## 13. 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| 502 Bad Gateway | 앱이 `$PORT` 가 아닌 7861 만 듣고 있음 → 1번 단계 수정 누락 |
| 페이지는 뜨는데 마이크 막힘 | 브라우저는 HTTPS 에서만 마이크 허용 → Railway 도메인은 HTTPS 라 정상. `http://` 로 접속하지 말 것 |
| WebSocket `wss://` 연결 실패 | 역시 `$PORT` 문제거나, Basic Auth 미들웨어가 WS 도 401 반환 → 페이지 먼저 로그인 후 WS 연결되어야 함 |
| 401 무한 루프 | `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` 오타 또는 양 끝 공백 확인 |
| `AZURE_OPENAI_API_KEY` 가 비어 있음 로그 | Variables 저장 후 자동 재배포가 끝났는지 확인. 안 됐으면 수동 Redeploy |

---

## 부록: 변경 파일 요약

- `app_realtime.py` — `$PORT` 처리 + (옵션) `BasicAuthMiddleware`
- `Procfile` — `web: python app_realtime.py`
- `.gitignore` — `.env` 보호
- `RAILWAY_DEPLOY.md` — 본 문서
