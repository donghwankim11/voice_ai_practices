#!/usr/bin/env bash
# voice_ai_practices 노트북 환경 셋업 스크립트
#
# 사용:
#   bash setup_env.sh
#   (또는 chmod +x setup_env.sh && ./setup_env.sh)
#
# 동작:
#   1. (선택) 활성화된 conda env 빠져나오기
#   2. python 3.10 conda env 생성/활성화
#   3. uv 설치 + conda env 를 활성 venv 로 인식시키기
#   4. (필요 시) requirements.in -> requirements.txt 잠금 컴파일
#      (이 부분은 사용자가 직접 수행하므로 기본 비활성화)
#   5. uv pip sync 로 requirements.txt 동기화
#   6. ipykernel 등록 (Jupyter 에서 voice_ai_practices 선택)

set -euo pipefail

ENV_NAME="voice_ai_practices"
PY_VER="3.10"

# ---------------------------------------------------------------------------
# conda 셸 함수 활성화 (subshell 에서도 conda activate 가 동작하도록)
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
# 2) uv 준비 (env 안에 pip 로 설치)
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
#    사용자가 직접 pip-compile 로 수행할 예정이므로 기본 비활성화.
#    필요 시 아래 라인 주석 해제하여 사용.
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

# ---------------------------------------------------------------------------
# 6) Jupyter 커널 등록
#    노트북 metadata 의 kernelspec.name 이 '${ENV_NAME}' 이면
#    Jupyter 가 자동으로 이 커널을 선택해 연결한다.
# ---------------------------------------------------------------------------
echo "[INFO] ipykernel 커널 등록"
python -m ipykernel install --user \
  --name "${ENV_NAME}" \
  --display-name "${ENV_NAME}"

echo
echo "[DONE] 환경 준비 완료."
echo "       Jupyter 에서 test_openai_apis.ipynb 을 열면 커널 '${ENV_NAME}' 이 자동 선택됩니다."
echo "       (수동 선택 필요 시: Kernel > Change Kernel > ${ENV_NAME})"
