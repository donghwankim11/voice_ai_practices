#!/usr/bin/env bash
# voice_ai_practices / openai_based_LCSK 환경 셋업 스크립트
#
# 사용:
#   bash setup_env.sh
#   (또는 chmod +x setup_env.sh && ./setup_env.sh)
#
# 동작:
#   1. (선택) 활성화된 conda env 빠져나오기
#   2. python 3.10 conda env 생성/활성화
#   3. uv 설치 + conda env 를 활성 venv 로 인식시키기
#   4. (필요 시) requirements.in -> requirements.txt 잠금 컴파일 (기본 비활성)
#   5. uv pip sync 로 requirements.txt 동기화

set -euo pipefail

ENV_NAME="voice_ai_practices"   # 상위 폴더와 같은 env 공유
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
# 4) (선택) requirements.in -> requirements.txt 잠금 컴파일
# ---------------------------------------------------------------------------
# echo "[INFO] requirements.txt 컴파일"
# uv pip compile requirements.in -o requirements.txt --python-version "${PY_VER}"

# ---------------------------------------------------------------------------
# 5) requirements.txt 동기화
# ---------------------------------------------------------------------------
if [[ ! -f "requirements.txt" ]]; then
  echo "[ERROR] requirements.txt 가 없습니다. 먼저 다음을 수행하세요:" >&2
  echo "        uv pip compile requirements.in -o requirements.txt --python-version ${PY_VER}" >&2
  exit 1
fi
echo "[INFO] uv pip sync requirements.txt"
uv pip sync requirements.txt

echo
echo "[DONE] 환경 준비 완료."
echo "       실행: conda activate ${ENV_NAME} && python app_realtime.py"
echo "       접속: http://localhost:7861"
