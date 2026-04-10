#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo
echo "============================================================"
echo "  Omni-Local RAG  --  System Function Tests"
echo "============================================================"
echo

if [[ ! -f main.py ]]; then
    echo "[ERROR] Run this script from inside the OmniLocalRAG folder."
    echo "        Expected main.py next to this shell script."
    exit 1
fi

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        PY="python3"
    elif command -v python >/dev/null 2>&1; then
        PY="python"
    else
        echo "[ERROR] Python not found. Install Python 3.10/3.11/3.12 or activate venv."
        exit 1
    fi
fi

echo "[OK] $($PY --version) (via $PY)"

export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

echo
echo "[INFO] Running model loading, ingest/export, retrieval fallback, and SQLite metadata tests ..."
echo

"$PY" -m unittest \
    tests.test_embed_manager_device \
    tests.test_inference_retrieval_fallback \
    tests.test_llm_manager_load \
    tests.test_pdf_vector_export \
    tests.test_qa_memory \
    tests.test_sqlite_metadata_store \
    tests.test_video_asr_worker

echo
echo "============================================================"
echo "  [DONE] System function tests passed."
echo "============================================================"
