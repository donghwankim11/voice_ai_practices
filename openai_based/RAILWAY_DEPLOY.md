# Railway 배포 가이드 — `app_realtime.py` (Azure OpenAI Realtime + 서버 VAD)

`openai_based/app_realtime.py` (FastAPI + WebSocket + Azure OpenAI Realtime) 를 Railway 에 올려
`https://<your-app>.up.railway.app` 으로 외부에서 접속하기 위한 절차.

> 같은 구조 가이드
> - 스킬 분리 버전: `openai_skills_based/RAILWAY_DEPLOY.md`
> - ElevenLabs 버전: `elevenlabs_based/RAILWAY_DEPLOY.md`

Railway 가 적합한 이유:
- WebSocket 정상 지원 (Realtime API 양방향 스트리밍 OK)
- HTTPS 자동 부여 → 브라우저 마이크(`getUserMedia`) 동작
- 환경변수로 Azure 키 안전 보관
- GitHub 푸시 → 자동 빌드/배포

## 0. 사전 준비

- Railway 계정: <https://railway.app> (GitHub 로그인)
- 이 디렉터리가 GitHub 리포에 올라가 있어야 함 → 본 리포: `donghwankim11/voice_ai_practices`
- 로컬에서 `python app_realtime.py` 가 정상 동작하는 상태 (포트 7861)
- Azure OpenAI 리소스에 `gpt-realtime-mini` deployment 가 활성 상태

---

## 1. 코드 수정 — 포트는 `$PORT` 에서 받기 (필수, 이미 적용됨)

Railway 는 컨테이너에 임의 포트를 `$PORT` 환경변수로 주입한다. 7861 하드코딩이면 외부 접근이 안 됨.

`app_realtime.py` 마지막 블록:

```python
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "7861"))  # Railway 는 PORT 주입, 로컬은 7861 유지
    uvicorn.run(app, host="0.0.0.0", port=port)
```

> 로컬 개발은 그대로 7861 로 동작, Railway 에서는 자동으로 주입된 포트를 사용.

---

## 2. 시작 명령 정의 — `Procfile` (이미 적용됨)

`openai_based/Procfile`:

```
web: python app_realtime.py
```

Railway 는 `Procfile` 의 `web:` 명령을 자동 실행. Railpack 이 `requirements.txt` 를 인식해
의존성 설치까지 처리.

---

## 3. `.gitignore` 점검 — `.env` 절대 커밋 금지

리포 루트 `.gitignore` 에 다음 포함 확인 (이미 설정됨):

```
.env
__pycache__/
```

`.env` 가 이미 커밋된 상태라면 즉시 git 히스토리에서 제거하고 Azure 키를 재발급할 것.

---

## 4. (강력 권장) 간단한 접근 보호 — HTTP Basic Auth (이미 적용됨)

Railway 의 공개 URL 은 **알면 누구나 접속 가능**. Azure 토큰을 외부인이 갉아먹지 않게
최소한 Basic Auth 게이트가 미들웨어로 적용되어 있다.

```python
BASIC_USER = os.getenv("BASIC_AUTH_USER")
BASIC_PASS = os.getenv("BASIC_AUTH_PASS")
# ...
class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if _check_basic_auth(request.headers.get("authorization")):
            return await call_next(request)
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="baemin-openai"'})

app.add_middleware(BasicAuthMiddleware)
```

WebSocket 도 같은 origin 의 Basic Auth 자격증명을 자동으로 실어 보내므로 보호된다.

> 로컬에서는 `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` 를 안 정하면 자동으로 비활성화되어
> 평소처럼 띄울 수 있다.

---

## 5. GitHub 에 푸시

본 디렉터리에 있는 `sync_to_github.sh` 가 PycharmProjects 워크스페이스 → GitHub clone 동기화 + commit + push 를
한 번에 처리한다:

```bash
cd /Users/donghwan.kim/PycharmProjects/nlp_experiments/voice_ai_practices/openai_based
./sync_to_github.sh "Railway 배포 준비(openai_based): PORT 환경변수, Procfile, Basic Auth"
```

---

## 6. Railway 프로젝트 생성 & GitHub 연결

1. <https://railway.app/new> → **Deploy from GitHub repo**
2. `donghwankim11/voice_ai_practices` 선택 → 권한 부여
3. **모노리포(`openai_based` 외에 다른 폴더 존재)** 이므로 **Root Directory 를 반드시 지정**해야 함.
   설정 안 하면 첫 빌드에서 다음 에러로 실패:

   ```
   ⚠ Script start.sh not found
   ✖ Railpack could not determine how to build the app.

   The app contents that Railpack analyzed contains:
   ./
   ├── openai_based/
   ├── openai_skills_based/
   ├── elevenlabs_based/
   └── .gitignore
   ```

   (Railway 가 리포 루트만 보기 때문에 Python 파일을 못 찾음.)

### 6-1. Root Directory 지정 방법

Railway UI 가 자주 바뀌어서 메뉴 경로가 헷갈릴 수 있다. 두 가지 방법 중 편한 쪽:

**(A) Settings 메뉴에서 직접 지정**
- `Settings → Service → Root Directory` (또는 `Settings → Source → Root Directory`,
  버전에 따라 위치가 다름) 에 `openai_based` 입력 후 저장.

**(B) Railway 우측 Agent 챗봇으로 자연어 지정 (UI 에서 못 찾을 때 — 실제 검증됨)**
- 프로젝트 화면 우측의 Railway AI Agent 패널에 다음과 같이 입력:
  > Set the root directory to `openai_based`

  Agent 가 자동으로 설정을 변경하고 재배포를 트리거한다.

### 6-2. (옵션) 추가 설정

- **Build → Builder**: Railway 는 현재 `Railpack` 이 기본 (구 Nixpacks). `requirements.txt`
  존재만으로 Python 으로 자동 인식되므로 별도 지정 불요.
- **Deploy → Start Command**: `Procfile` 의 `web: python app_realtime.py` 가 자동 인식되므로
  비워둬도 됨. (안 잡힐 때만 `python app_realtime.py` 수동 입력)

---

## 7. 환경변수 설정 — **반드시 dashboard 에서**

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

## 8. 공개 URL 발급

**Settings → Networking → Generate Domain** 클릭.
`https://<random>.up.railway.app` 형식의 HTTPS 도메인이 나온다.

브라우저로 접속 → Basic Auth 로그인 창 → 통과하면 마이크 권한 → 통화 시작.

---

## 9. 동작 점검 체크리스트

- [ ] `https://<...>.up.railway.app` 접속 시 401 → 로그인창 → 정상 통과
- [ ] 인덱스 페이지 로드, 통화 시작 버튼 표시
- [ ] 통화 시작 시 마이크 권한 팝업
- [ ] 브라우저 개발자도구 Network 탭에서 `wss://` 로 WebSocket 연결됨 (101 Switching Protocols)
- [ ] AGENT 첫 발화가 음성으로 재생됨
- [ ] 끼어들기(인터럽트) 동작
- [ ] Railway → **Deployments → View Logs** 에 `[ws] client connected` 출력

---

## 10. (옵션) 커스텀 도메인 + Cloudflare Access 이중 보호

Basic Auth 만으로 부족하다면, Railway 도메인 위에 Cloudflare 를 한 겹 더 씌울 수 있다.

1. Railway → **Settings → Networking → Custom Domain** 에 `agent.example.com` 추가
2. 안내된 CNAME 을 Cloudflare DNS 에 등록 (proxy ON 가능)
3. Cloudflare Zero Trust → Access → Applications 에서 `agent.example.com` 에
   이메일 OTP 또는 SSO 정책 부여

---

## 11. 운영 팁 / 주의사항

- **콜드 스타트**: Free/Hobby 플랜은 일정 시간 미사용 시 슬립. 첫 요청이 5~10초 늦어질 수 있다.
- **로그**: `print(...)` 출력은 Railway Logs 에서 실시간 확인. 전사문 등 민감 정보 누설 점검 권장.
- **Azure 비용**: Realtime API 호출 비용은 Azure 측. Basic Auth 비번 강하게.
- **재배포**: GitHub `main` 푸시 시 자동 재빌드. 수동은 **Deployments → Redeploy**.

---

## 12. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `Railpack could not determine how to build` | Root Directory 미지정 — 6-1 단계 확인 |
| 502 Bad Gateway | 앱이 `$PORT` 가 아닌 7861 만 듣고 있음 → 1번 단계 누락 |
| 페이지는 뜨는데 마이크 막힘 | HTTPS 에서만 마이크 허용 → `http://` 가 아니라 `https://` 로 접속 |
| WebSocket `wss://` 연결 실패 | `$PORT` 문제거나, Basic Auth 미들웨어가 WS 도 401 반환 → 페이지 먼저 로그인 후 WS 연결되어야 함 |
| 401 무한 루프 | `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` 오타 또는 양 끝 공백 확인 |
| `AZURE_OPENAI_API_KEY` 비어 있음 로그 | Variables 저장 후 자동 재배포가 끝났는지 확인. 안 됐으면 수동 Redeploy |

---

## 부록: 변경 파일 요약

- `openai_based/app_realtime.py` — `$PORT` 처리 + `BasicAuthMiddleware`
- `openai_based/Procfile` — `web: python app_realtime.py`
- `openai_based/RAILWAY_DEPLOY.md` — 본 문서
- `openai_based/sync_to_github.sh` — 워크스페이스 → GitHub 동기화 스크립트
- (루트) `.gitignore` — `.env` 보호 (이미 적용)
