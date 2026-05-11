#!/usr/bin/env bash
# elevenlabs_based 환경 셋업 스크립트
#
# 사용:
#   bash setup_env.sh
#   (또는 chmod +x setup_env.sh && ./setup_env.sh)
#
# 동작:
#   1. (선택) 활성화된 conda env 빠져나오기
#   2. python 3.10 conda env (voice_ai_elevenlabs) 생성/활성화
#   3. uv 설치 + conda env 를 활성 venv 로 인식시키기
#   4. requirements.txt 로 의존성 동기화 (없으면 in 으로부터 컴파일)
#   5. ipykernel 등록

set -euo pipefail

ENV_NAME="voice_ai_elevenlabs"
PY_VER="3.10"

# ---------------------------------------------------------------------------
# conda 셸 함수 활성화
# ---------------------------------------------------------------------------
if ! command -v conda >/dev/null 2>&1; then
  echo "[ERROR] conda 가 PATH 에 없습니다. miniconda/anaconda 를 먼저 설치하세요." >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

# ---------------------------------------------------------------------------
# 0) 다른 env 활성화 상태라면 빠져나오기 (base 까지)
# ---------------------------------------------------------------------------
while [[ "${CONDA_DEFAULT_ENV:-}" != "" && "${CONDA_DEFAULT_ENV}" != "base" ]]; do
  conda deactivate
done

# ---------------------------------------------------------------------------
# 1) conda env 생성 / 활성화 (Python 3.10)
# ---------------------------------------------------------------------------
if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  echo "[INFO] conda env '${ENV_NAME}' 이미 존재 → 그대로 사용"
else
  echo "[INFO] conda env '${ENV_NAME}' 생성 (python=${PY_VER})"
  conda create --name "${ENV_NAME}" python="${PY_VER}" -y
fi
conda activate "${ENV_NAME}"

# ---------------------------------------------------------------------------
# 2) uv 준비
# ---------------------------------------------------------------------------
if ! python -c "import uv" >/dev/null 2>&1 && ! command -v uv >/dev/null 2>&1; then
  echo "[INFO] uv 설치"
  pip install --quiet uv
else
  echo "[INFO] uv 이미 사용 가능"
fi

# ---------------------------------------------------------------------------
# 3) uv 에게 현재 conda env 를 활성 venv 로 인식시킨다
# ---------------------------------------------------------------------------
export VIRTUAL_ENV="${CONDA_PREFIX}"

# ---------------------------------------------------------------------------
# 4) requirements.txt 동기화 (없으면 .in 에서 컴파일)
# ---------------------------------------------------------------------------
if [[ ! -f "requirements.txt" ]]; then
  echo "[INFO] requirements.txt 가 없어 requirements.in 에서 컴파일"
  uv pip compile requirements.in -o requirements.txt --python-version "${PY_VER}"
fi
echo "[INFO] uv pip sync requirements.txt"
uv pip sync requirements.txt

# ---------------------------------------------------------------------------
# 5) ipykernel 등록
# ---------------------------------------------------------------------------
echo "[INFO] ipykernel 커널 등록"
pip install --quiet ipykernel
python -m ipykernel install --user \
  --name "${ENV_NAME}" \
  --display-name "${ENV_NAME}"

echo
echo "[DONE] 환경 준비 완료."
echo "       1) .env 작성 (.env.example 참고)"
echo "       2) python create_agent.py  (agent 생성 → .env 에 ELEVENLABS_AGENT_ID 자동 추가)"
echo "       3) python app_realtime.py  (http://localhost:7862)"
