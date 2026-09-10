#!/usr/bin/env bash
#
# Produce a single archive that installs this system on a machine with no
# internet connection.
#
# This script is the sovereignty claim made concrete. The system is supposed to
# run inside a refinery's network with no route out, and the honest test of that
# is not a firewall rule — it is whether the thing can be *installed* without
# one. Anything this bundle forgets becomes a download request on a plant network
# that does not permit downloads, which is how "air-gapped" systems quietly grow
# an exception.
#
# What goes in:
#   - container images, exported as tarballs (postgres, qdrant, the sandbox)
#   - every Python wheel, for this platform, resolved from the lockfile
#   - the frontend, already built, plus its node_modules-free static output
#   - the OCR ONNX models, which RapidOCR otherwise fetches on first use
#   - the source tree, configuration, migrations and seed corpus
#
# What deliberately does NOT go in: the LLM weights. They are 5-30 GB each,
# they are the one component the operator is most likely to already have, and
# baking them in would make the bundle unusable over the media that actually
# gets carried into a plant. install.sh checks for them and says exactly which
# are missing.
#
# Usage:  bash scripts/airgap_bundle.sh [output-directory]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/dist}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BUNDLE="workbench-airgap-${STAMP}"
STAGE="${OUT_DIR}/${BUNDLE}"

PG_IMAGE="postgres:16-alpine"
QDRANT_IMAGE="qdrant/qdrant:v1.12.4"
SANDBOX_IMAGE="workbench/sandbox:0.1.0"

log()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m warn\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m error\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker is required to export the container images"
command -v uv     >/dev/null || die "uv is required to resolve the Python wheels"

log "Staging into ${STAGE}"
rm -rf "${STAGE}"
mkdir -p "${STAGE}"/{images,wheels,models,web,source}

# --- container images --------------------------------------------------------
# Saved rather than pulled. `docker save` writes the layers themselves, so the
# target machine never contacts a registry.
log "Exporting container images"
for image in "${PG_IMAGE}" "${QDRANT_IMAGE}" "${SANDBOX_IMAGE}"; do
    name="$(echo "${image}" | tr '/:' '__')"
    if ! docker image inspect "${image}" >/dev/null 2>&1; then
        if [ "${image}" = "${SANDBOX_IMAGE}" ]; then
            log "  building ${image}"
            docker build -t "${image}" "${REPO_ROOT}/services/sandbox"
        else
            log "  pulling ${image}"
            docker pull "${image}"
        fi
    fi
    log "  saving ${image}"
    docker save "${image}" | gzip -1 > "${STAGE}/images/${name}.tar.gz"
done

# --- python wheels -----------------------------------------------------------
# Wheels for *this* platform. A bundle built on macOS will not install on the
# Linux box in the plant, so the platform is recorded and install.sh checks it.
log "Downloading Python wheels"
uv export --frozen --no-dev --format requirements-txt \
    --project "${REPO_ROOT}/apps/api" > "${STAGE}/wheels/requirements.txt" 2>/dev/null \
    || uv export --frozen --no-dev --format requirements-txt > "${STAGE}/wheels/requirements.txt"

uv pip download --requirements "${STAGE}/wheels/requirements.txt" \
    --dest "${STAGE}/wheels" >/dev/null 2>&1 \
    || python3 -m pip download --requirement "${STAGE}/wheels/requirements.txt" \
         --dest "${STAGE}/wheels" >/dev/null

python3 - "${STAGE}" <<'PY'
import json, platform, sys
from pathlib import Path
stage = Path(sys.argv[1])
(stage / "PLATFORM.json").write_text(json.dumps({
    "system": platform.system(),
    "machine": platform.machine(),
    "python": ".".join(platform.python_version_tuple()[:2]),
}, indent=2) + "\n")
PY

# --- OCR models --------------------------------------------------------------
# RapidOCR downloads its ONNX weights on first use. On an air-gapped machine
# that first use is a stack trace, and it happens during the first document
# upload rather than at install time, which is the worst moment to discover it.
log "Collecting OCR models"
OCR_FOUND=0
for candidate in \
    "${HOME}/.cache/rapidocr" \
    "${HOME}/.rapidocr" \
    "$(python3 -c 'import rapidocr_onnxruntime as m, pathlib; print(pathlib.Path(m.__file__).parent / "models")' 2>/dev/null || true)"
do
    if [ -n "${candidate}" ] && [ -d "${candidate}" ]; then
        cp -R "${candidate}"/. "${STAGE}/models/" 2>/dev/null && OCR_FOUND=1 && break
    fi
done
[ "${OCR_FOUND}" -eq 1 ] || warn "no RapidOCR models found — run an OCR ingest once, then rebuild the bundle"

# --- frontend ----------------------------------------------------------------
log "Building the frontend"
if command -v pnpm >/dev/null && [ -d "${REPO_ROOT}/apps/web" ]; then
    (cd "${REPO_ROOT}" && pnpm install --frozen-lockfile >/dev/null 2>&1 && pnpm --filter web build >/dev/null 2>&1) \
        && cp -R "${REPO_ROOT}/apps/web/.next" "${STAGE}/web/.next" 2>/dev/null \
        && cp -R "${REPO_ROOT}/apps/web/public" "${STAGE}/web/public" 2>/dev/null \
        || warn "frontend build failed — the bundle will ship source only"
else
    warn "pnpm not found — the bundle will ship the frontend as source"
fi

# --- source ------------------------------------------------------------------
log "Copying the source tree"
tar -C "${REPO_ROOT}" \
    --exclude='.git' --exclude='.venv' --exclude='node_modules' --exclude='dist' \
    --exclude='__pycache__' --exclude='.next' --exclude='.pytest_cache' \
    --exclude='.mypy_cache' --exclude='.ruff_cache' --exclude='data/blobs' \
    -czf "${STAGE}/source/workbench-source.tar.gz" .

# --- installer ---------------------------------------------------------------
cat > "${STAGE}/install.sh" <<'INSTALL'
#!/usr/bin/env bash
# Install the workbench from this bundle. No network access is required or used.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-/opt/workbench}"

log() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m error\033[0m %s\n' "$*" >&2; exit 1; }

# The wheels are platform-specific, so a mismatch is a hard stop rather than a
# confusing compilation failure forty seconds later.
python3 - "${HERE}" <<'PY'
import json, platform, sys
from pathlib import Path
want = json.loads((Path(sys.argv[1]) / "PLATFORM.json").read_text())
have = {"system": platform.system(), "machine": platform.machine(),
        "python": ".".join(platform.python_version_tuple()[:2])}
if (want["system"], want["machine"]) != (have["system"], have["machine"]):
    sys.exit(f"this bundle was built for {want['system']}/{want['machine']}, "
             f"this machine is {have['system']}/{have['machine']}")
if want["python"] != have["python"]:
    sys.exit(f"this bundle needs Python {want['python']}, found {have['python']}")
PY

command -v docker >/dev/null || die "docker is required"

log "Loading container images"
for tarball in "${HERE}"/images/*.tar.gz; do
    gunzip -c "${tarball}" | docker load
done

log "Unpacking the source into ${TARGET}"
mkdir -p "${TARGET}"
tar -xzf "${HERE}/source/workbench-source.tar.gz" -C "${TARGET}"

log "Installing Python packages from the bundled wheels"
python3 -m venv "${TARGET}/.venv"
"${TARGET}/.venv/bin/pip" install --no-index --find-links "${HERE}/wheels" \
    --requirement "${HERE}/wheels/requirements.txt"

if [ -d "${HERE}/models" ] && [ -n "$(ls -A "${HERE}/models" 2>/dev/null)" ]; then
    log "Installing OCR models"
    mkdir -p "${HOME}/.cache/rapidocr"
    cp -R "${HERE}/models"/. "${HOME}/.cache/rapidocr/"
fi

if [ -d "${HERE}/web/.next" ]; then
    log "Installing the built frontend"
    cp -R "${HERE}/web/.next" "${TARGET}/apps/web/.next"
    [ -d "${HERE}/web/public" ] && cp -R "${HERE}/web/public" "${TARGET}/apps/web/public"
fi

log "Checking for model weights"
"${TARGET}/.venv/bin/python" - "${TARGET}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "apps/api/src"))
try:
    from workbench.providers.registry import ModelRegistry
except Exception as exc:  # noqa: BLE001
    print(f"  could not inspect the manifest: {exc}")
    raise SystemExit(0)

manifest = Path(sys.argv[1]) / "config/models.yaml"
try:
    registry = ModelRegistry(manifest)
except Exception as exc:  # noqa: BLE001
    print(f"  could not read {manifest}: {exc}")
    raise SystemExit(0)

wanted = sorted({m.physical_id for m in registry.models.values()})
print("  this bundle carries no model weights, by design. Required:")
for name in wanted:
    print(f"    - {name}")
print("  load them into LM Studio or Ollama on this machine before first use.")
PY

cat <<'DONE'

Installed. Next:
  1. Load the model weights listed above into LM Studio or Ollama.
  2. cd TARGET && docker compose up -d postgres qdrant
  3. make migrate && make seed-db
  4. make dev

Nothing above contacts the internet.
DONE
INSTALL
chmod +x "${STAGE}/install.sh"

# --- manifest and checksums --------------------------------------------------
PLATFORM="$(python3 -c 'import platform; print(platform.system() + "/" + platform.machine())')"

log "Writing the manifest"
(cd "${STAGE}" && find . -type f ! -name SHA256SUMS -exec shasum -a 256 {} + | sort -k2 > SHA256SUMS)

cat > "${STAGE}/README.md" <<EOF
# Sovereign AI Workbench — air-gap bundle

Built ${STAMP} for ${PLATFORM}.

    bash install.sh [/opt/workbench]

Verify first:

    shasum -a 256 -c SHA256SUMS

Contains the container images, every Python wheel, the OCR models, the built
frontend and the full source. It does **not** contain the LLM weights — see
\`config/models.yaml\` for what to load into LM Studio or Ollama. The installer
lists them and refuses nothing else.
EOF

log "Compressing"
(cd "${OUT_DIR}" && tar -czf "${BUNDLE}.tar.gz" "${BUNDLE}")
SIZE="$(du -h "${OUT_DIR}/${BUNDLE}.tar.gz" | cut -f1)"

log "Done: ${OUT_DIR}/${BUNDLE}.tar.gz (${SIZE})"
