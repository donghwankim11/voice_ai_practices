# AWS EC2 배포 가이드 — `app_realtime.py`

`app_realtime.py` (FastAPI + WebSocket + Azure OpenAI Realtime) 를
**AWS EC2 + Caddy(자동 HTTPS) + systemd** 로 24/7 서빙하는 절차.

목표 아키텍처:

```
[브라우저]
   │  HTTPS / WSS  (도메인: agent.example.com)
   ▼
[Caddy :443]  ──►  [Uvicorn(FastAPI) :7861 on localhost]
   │ Let's Encrypt 자동 발급/갱신
   │ Basic Auth 게이트
   │ WebSocket 자동 업그레이드
   ▼
[Azure OpenAI Realtime]   ← 키는 .env 파일에만
```

> Nginx 가 익숙하다면 13절에 동일 동작의 Nginx 설정 제공.

---

## 0. 사전 준비물

- AWS 계정 + 결제수단
- 도메인 1개 (Route53/가비아/Cloudflare 등 — 본인이 DNS 관리할 수 있어야 함)
- 로컬 SSH 클라이언트(맥은 기본 내장)
- 이 프로젝트가 GitHub 리포에 있으면 배포가 깔끔. 없으면 `scp` 로 업로드.

예시 가정:
- 도메인: `agent.example.com`
- 리전: `ap-northeast-2` (서울)
- 사용자: Ubuntu 24.04 LTS

---

## 1. EC2 인스턴스 생성

AWS 콘솔 → EC2 → **Launch instance**.

| 항목 | 값 |
|---|---|
| Name | `baemin-agent` |
| AMI | **Ubuntu Server 24.04 LTS (HVM), SSD** (x86_64) |
| Instance type | **t3.small** (vCPU 2, RAM 2GB) — 권장. 비용 우선이면 t3.micro |
| Key pair | 새로 생성하거나 기존 키 선택. `.pem` 파일 안전하게 보관 |
| Network settings → **Edit** | VPC/서브넷 기본값 사용. **Auto-assign public IP: Enable** |
| Security group | **Create new** → 아래 표대로 |
| Storage | 16 GiB gp3 |

**Security Group 인바운드 규칙**:

| Type | Port | Source | 설명 |
|---|---|---|---|
| SSH | 22 | **My IP** | 본인 IP만 (콘솔 자동 채움) |
| HTTP | 80 | 0.0.0.0/0 | Let's Encrypt 챌린지용 |
| HTTPS | 443 | 0.0.0.0/0 | 실제 서비스 |

→ **Launch instance**.

---

## 2. Elastic IP 할당 (권장)

인스턴스 재시작해도 IP가 안 바뀌도록.

EC2 → **Elastic IPs** → **Allocate** → 그 IP 선택 → **Actions → Associate** →
방금 만든 인스턴스 선택. 이 IP를 도메인 A 레코드에 사용.

---

## 3. DNS A 레코드 등록

본인 DNS 관리 화면에서:

```
agent.example.com   A   <Elastic IP>   TTL 300
```

전파 확인 (로컬에서):

```bash
dig +short agent.example.com
# → Elastic IP 가 떠야 함
```

---

## 4. SSH 접속

```bash
chmod 400 ~/Downloads/baemin-agent.pem   # 처음 한 번만
ssh -i ~/Downloads/baemin-agent.pem ubuntu@<Elastic IP>
```

---

## 5. 서버 기본 패키지 설치

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3.12-venv python3-pip git ufw
```

> Ubuntu 24.04 의 기본 Python은 3.12. `requirements.txt` 가 3.10 기준으로
> 컴파일됐어도 호환되는 버전 차이만 있으면 거의 그대로 동작. 문제 시 12절 참고.

방화벽 (선택, 보안그룹과 이중화):

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

---

## 6. 코드 배포

### 옵션 A) GitHub 에서 클론 (권장)

```bash
cd ~
git clone https://github.com/<your-org>/<your-repo>.git
cd <your-repo>/voice_ai_practices/openai_skills_based
```

### 옵션 B) 로컬에서 scp 업로드

로컬 셸에서:

```bash
cd /Users/donghwan.kim/PycharmProjects/nlp_experiments
rsync -avz --exclude __pycache__ --exclude .env \
  -e "ssh -i ~/Downloads/baemin-agent.pem" \
  voice_ai_practices/openai_skills_based \
  ubuntu@<Elastic IP>:~/baemin-agent/
```

서버 작업 디렉터리:
```bash
ssh ...
cd ~/baemin-agent/openai_skills_based   # 또는 위 경로
```

---

## 7. 가상환경 + 의존성 설치

```bash
python3 -m venv ~/.venv
source ~/.venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 8. `.env` 파일 — 키는 서버에서만

```bash
cat > ~/baemin-agent.env <<'EOF'
AZURE_OPENAI_API_KEY=실제키값
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
EOF
chmod 600 ~/baemin-agent.env
```

> 절대 git 에 커밋 금지. 코드의 `load_dotenv()` 와 별도로, systemd 의
> `EnvironmentFile=` 로 주입할 거라 `app_realtime.py` 옆에 `.env` 가 없어도 됨.

---

## 9. 동작 테스트 (포그라운드)

```bash
cd ~/baemin-agent/openai_skills_based   # 또는 git 클론 경로
source ~/.venv/bin/activate
set -a; source ~/baemin-agent.env; set +a
python app_realtime.py
# → http://0.0.0.0:7861
```

다른 터미널에서:
```bash
ssh ...
curl -I http://localhost:7861/
# HTTP/1.1 200 OK 가 떠야 함
```

OK 면 `Ctrl+C` 로 종료.

---

## 10. systemd 서비스 등록 — 부팅 시 자동 실행 + 자동 재시작

```bash
sudo tee /etc/systemd/system/baemin-agent.service > /dev/null <<'EOF'
[Unit]
Description=Baemin Realtime Voice Agent
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/baemin-agent/openai_skills_based
EnvironmentFile=/home/ubuntu/baemin-agent.env
ExecStart=/home/ubuntu/.venv/bin/python app_realtime.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# git 클론 경로면 WorkingDirectory 를 그쪽으로 바꿀 것
sudo systemctl daemon-reload
sudo systemctl enable --now baemin-agent
sudo systemctl status baemin-agent     # active(running) 확인
journalctl -u baemin-agent -f          # 실시간 로그 (Ctrl+C 로 빠져나오기)
```

---

## 11. Caddy 설치 — 자동 HTTPS + WS + Basic Auth

Caddy 는 도메인만 맞으면 Let's Encrypt 인증서를 자동 발급/갱신하고,
WebSocket 도 추가 설정 없이 통과시킨다.

### 11-1. Caddy 설치

```bash
sudo apt -y install debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt -y install caddy
```

### 11-2. Basic Auth 비밀번호 해시 생성

```bash
caddy hash-password
# 입력: 본인이 정한 강한 패스워드
# 출력: $2a$14$.... ← 복사해둘 것
```

### 11-3. Caddyfile 작성

```bash
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
agent.example.com {
    encode zstd gzip

    basic_auth {
        agent $2a$14$여기에_위에서_복사한_해시
    }

    reverse_proxy localhost:7861 {
        # WebSocket 자동 업그레이드는 기본 동작
        transport http {
            read_timeout  10m
            write_timeout 10m
        }
    }
}
EOF
```

> `agent.example.com` 과 해시값을 실제 값으로 치환. `agent` 는 Basic Auth ID.

### 11-4. 적용

```bash
sudo systemctl reload caddy
sudo systemctl status caddy
journalctl -u caddy -f      # 인증서 발급 로그 확인
```

수십 초 내 Let's Encrypt 인증서가 발급된다.

---

## 12. 동작 검증

브라우저에서 `https://agent.example.com` 접속:

1. ✅ 자물쇠 표시(HTTPS) + 로그인창 (Basic Auth)
2. ✅ ID/PW 통과 후 인덱스 페이지
3. ✅ "통화 시작" → 마이크 권한 → 통화 진행
4. ✅ DevTools → Network → `wss://agent.example.com/ws` 가 101 Switching Protocols

서버 로그 동시 확인:
```bash
journalctl -u baemin-agent -f
```
`[ws] client connected`, `[skill] load_skill('...')` 가 떠야 정상.

---

## 13. (대안) Caddy 대신 Nginx 쓰기

이미 Nginx 로 다른 서비스 운영 중이라면.

```bash
sudo apt -y install nginx certbot python3-certbot-nginx

sudo tee /etc/nginx/sites-available/baemin-agent > /dev/null <<'EOF'
server {
    listen 80;
    server_name agent.example.com;

    auth_basic "baemin-agent";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass         http://127.0.0.1:7861;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # WebSocket 업그레이드 (필수)
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        "upgrade";

        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/baemin-agent /etc/nginx/sites-enabled/
sudo apt -y install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd agent       # 비번 입력
sudo nginx -t && sudo systemctl reload nginx

# HTTPS 자동 발급 (80 → 443 리다이렉트 포함)
sudo certbot --nginx -d agent.example.com
```

certbot 이 자동 갱신 cron/systemd timer 까지 등록해준다.

---

## 14. 운영 명령 치트시트

```bash
# 앱 재시작 (코드 수정 후)
sudo systemctl restart baemin-agent

# 코드 업데이트 (git 클론 시)
cd ~/<repo>/voice_ai_practices/openai_skills_based
git pull
source ~/.venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart baemin-agent

# 로그 보기
journalctl -u baemin-agent -f               # 앱 로그
journalctl -u caddy -f                      # 리버스 프록시 로그

# Caddyfile 변경 후
sudo systemctl reload caddy

# 인스턴스 재부팅 (양쪽 다 자동 기동됨)
sudo reboot
```

---

## 15. (옵션) Cloudflare 를 앞에 두기 — Access + DDoS 보호

도메인 NS 를 Cloudflare 로 옮긴 뒤:

1. DNS 레코드 `agent.example.com → <Elastic IP>` 의 **Proxy: 켜기** (오렌지 구름)
2. Zero Trust → Access → Applications → `agent.example.com` 에 이메일 OTP / SSO 정책
3. 결과: Cloudflare Access 로그인 → Caddy Basic Auth → 앱. 이중 보호 + 오리진 IP 가림.

추가로 EC2 보안그룹의 80/443 을 **Cloudflare IP 대역만** 허용으로 좁히면 더 강함
(<https://www.cloudflare.com/ips-v4>). 단, 인증서 갱신은 Caddy 가 80 으로
Let's Encrypt 챌린지를 받으니 이때 Cloudflare IP 만 허용으로 막아도 정상 동작 (챌린지가 프록시 통해 들어옴).

---

## 16. 비용 가늠 (서울 리전 기준, 2026년 시세 변동 가능)

| 항목 | 대략 |
|---|---|
| EC2 t3.small (730h/월) | ~$16~18 |
| EBS gp3 16 GiB | ~$1.5 |
| Elastic IP (할당+사용 중일 때 무료, 미사용시 과금) | $0 |
| 아웃바운드 트래픽 | 첫 100GB 무료, 이후 GB당 ~$0.09 |
| **합계** | **월 ~$18~25** + Azure 호출비 |

비용을 더 줄이려면:
- t3.micro (RAM 1GB) — Realtime API 가 비교적 가벼워 가능. 단, 동시 통화 늘면 OOM 위험
- Savings Plans (1년 약정) 로 30~40% 절감

---

## 17. 보안 체크리스트

- [ ] `.pem` 키 로컬에 안전 보관, 권한 400
- [ ] 보안그룹 SSH(22) 는 **My IP** 로만 (전체 개방 X)
- [ ] `.env` 파일 권한 600, git 에 커밋 안 됨
- [ ] Basic Auth 비번 16자 이상 영숫자기호 혼합
- [ ] Caddy/Nginx 가 자동 HTTPS 리다이렉트 (HTTP → HTTPS) 동작
- [ ] `sudo unattended-upgrades` 활성화로 보안패치 자동 적용
  ```bash
  sudo apt -y install unattended-upgrades
  sudo dpkg-reconfigure -plow unattended-upgrades
  ```
- [ ] CloudWatch/journalctl 로그에 키나 전사 민감정보 흘리지 않기
- [ ] 사용 안 할 때 인스턴스 stop (EBS·EIP 비용은 계속, EC2 시간당 비용은 정지)

---

## 18. 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| `https://...` 가 인증서 오류 | DNS A 레코드 전파 안 됨 또는 80/443 보안그룹 차단. `dig agent.example.com` 확인, 보안그룹 점검 |
| 502 Bad Gateway | 앱이 죽음. `sudo systemctl status baemin-agent`, `journalctl -u baemin-agent -n 100` |
| 페이지는 뜨는데 마이크 막힘 | HTTPS 가 아닐 때만 발생. Caddy/Nginx HTTPS 적용 확인 |
| `wss://` 연결 실패 | Nginx 의 `Upgrade`/`Connection` 헤더 누락 (13절 설정 확인). Caddy 는 자동 |
| `AZURE_OPENAI_API_KEY` 없다고 에러 | systemd 의 `EnvironmentFile` 경로/권한 확인. `sudo systemctl show baemin-agent | grep Env` |
| pip install 시 `python-dotenv` 등 빌드 실패 | `sudo apt -y install build-essential python3.12-dev` 후 재시도 |
| 인스턴스 IP 바뀜 | Elastic IP 미할당. 2절 수행 |

---

## 부록: 추가/변경 파일 요약

서버측:
- `/etc/systemd/system/baemin-agent.service` — 앱 데몬화
- `/etc/caddy/Caddyfile` — HTTPS + Basic Auth + 리버스프록시
- `~/baemin-agent.env` — Azure 키 (권한 600)

로컬측: 코드 변경 **없음**. `app_realtime.py` 의 7861 포트 그대로 OK
(외부에는 Caddy 가 443 으로 노출).
