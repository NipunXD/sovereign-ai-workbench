# The sandbox image.
#
# Deliberately minimal: the scientific stack an engineering calculation needs,
# and nothing that could reach the network even if the container somehow had
# access to one. No curl, no wget, no git, no package manager left usable at
# runtime.
FROM python:3.12-slim

# Wheels only, and only what a calculation or chart actually needs. Every extra
# package is more attack surface inside a container running model-written code.
# No `|| true` here, deliberately. An earlier version ended this chain with it
# and a failing pip install produced an image containing nothing but pip — the
# build reported success and every execution failed later with
# ModuleNotFoundError. A broken image must fail the build.
RUN pip install --no-cache-dir --only-binary=:all: \
        numpy \
        pandas \
        matplotlib \
        openpyxl \
        pint \
    && rm -rf /root/.cache

# Confirm the stack imports at build time rather than discovering it is missing
# on the first execution.
RUN python -c "import numpy, pandas, matplotlib, openpyxl, pint; print('sandbox stack ok')"

# Runs as nobody. Combined with a read-only root filesystem, generated code
# cannot modify the image it runs in.
RUN mkdir -p /workspace/in /workspace/out && chown -R 65534:65534 /workspace

WORKDIR /workspace
COPY runner.py /runner.py

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    MPLBACKEND=Agg \
    HOME=/tmp \
    OPENBLAS_NUM_THREADS=2 \
    OMP_NUM_THREADS=2

USER 65534:65534
ENTRYPOINT ["python", "/runner.py"]
