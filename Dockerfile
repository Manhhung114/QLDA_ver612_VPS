FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    QLDA_RUNTIME_DATA_ROOT=/app/data/runtime \
    QLDA_PARALLEL_EXCEL_MIN_MB=2 \
    QLDA_PARALLEL_MIN_SHEETS=2 \
    QLDA_MP_START_METHOD=forkserver

RUN apt-get update && apt-get install -y --no-install-recommends \
      default-jre-headless \
      build-essential \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir -r requirements.lock

COPY . .

RUN python -m compileall -q src/qlda \
    && python -c "import qlda; print('QLDA package OK:', qlda.__version__)" \
    && python -c "from qlda.runtime_core import mpp_cloud_reader; mpp_cloud_reader._ensure_jvm(); print('MPP runtime OK')"

RUN groupadd --system qlda \
    && useradd --system --gid qlda --create-home --home-dir /home/qlda qlda \
    && mkdir -p /app/data/runtime \
    && chown -R qlda:qlda /app /home/qlda

ENV HOME=/home/qlda
USER qlda

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=8s --start-period=30s --retries=3 \
  CMD curl --fail --silent --show-error --max-time 8 http://127.0.0.1:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "src/qlda/presentation/streamlit/main.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
