#!/usr/bin/env bash
set -euo pipefail

APP_USER="okxsignal"
APP_GROUP="okxsignal"
APP_BASE="/opt/okx-signal"
APP_DIR="${APP_BASE}/app"
VENV_DIR="${APP_BASE}/venv"
ENV_DIR="/etc/okx-signal"
ENV_FILE="${ENV_DIR}/okx-signal.env"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_SERVICE=false

if [[ "${1:-}" == "--start" ]]; then
  START_SERVICE=true
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deployment/install_linux.sh [--start]" >&2
  exit 1
fi

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python 3.11 or newer is required" >&2
  exit 1
}

if ! getent group "${APP_GROUP}" >/dev/null; then
  groupadd --system "${APP_GROUP}"
fi
if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${APP_GROUP}" --home-dir "${APP_BASE}" --shell /usr/sbin/nologin "${APP_USER}"
fi

install -d -m 0750 -o "${APP_USER}" -g "${APP_GROUP}" "${APP_BASE}" "${APP_DIR}" "${ENV_DIR}"

# Copy only the reviewed release allow-list. Runtime data is preserved across upgrades.
RELEASE_MANIFEST="${SOURCE_DIR}/RELEASE_FILES.txt"
if [[ ! -f "${RELEASE_MANIFEST}" ]]; then
  echo "Missing reviewed release manifest: ${RELEASE_MANIFEST}" >&2
  exit 1
fi
find "${APP_DIR}" -mindepth 1 -maxdepth 1 ! -name outputs ! -name logs -exec rm -rf {} +
while IFS= read -r relative_path || [[ -n "${relative_path}" ]]; do
  [[ -z "${relative_path}" || "${relative_path}" == \#* ]] && continue
  if [[ "${relative_path}" == /* || "${relative_path}" == *".."* ]]; then
    echo "Unsafe release path: ${relative_path}" >&2
    exit 1
  fi
  source_path="${SOURCE_DIR}/${relative_path}"
  target_path="${APP_DIR}/${relative_path}"
  if [[ ! -f "${source_path}" ]]; then
    echo "Release file missing: ${relative_path}" >&2
    exit 1
  fi
  install -d -m 0750 -o "${APP_USER}" -g "${APP_GROUP}" "$(dirname "${target_path}")"
  cp -a "${source_path}" "${target_path}"
done < "${RELEASE_MANIFEST}"
install -d -m 0750 -o "${APP_USER}" -g "${APP_GROUP}" "${APP_DIR}/outputs" "${APP_DIR}/logs"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  python3 -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r "${APP_DIR}/requirements.lock"
"${VENV_DIR}/bin/python" -m pip install "${APP_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  install -m 0600 -o root -g "${APP_GROUP}" "${APP_DIR}/deployment/okx-signal.env.example" "${ENV_FILE}"
  echo "Created ${ENV_FILE}; configure the Feishu webhook and network paths before starting."
fi

install -m 0644 "${APP_DIR}/deployment/systemd/okx-signal.service" /etc/systemd/system/okx-signal.service
install -m 0644 "${APP_DIR}/deployment/systemd/okx-signal-health.service" /etc/systemd/system/okx-signal-health.service
install -m 0644 "${APP_DIR}/deployment/systemd/okx-signal-health.timer" /etc/systemd/system/okx-signal-health.timer
install -m 0644 "${APP_DIR}/deployment/logrotate/okx-signal" /etc/logrotate.d/okx-signal

chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}/outputs" "${APP_DIR}/logs"
chmod 0750 "${APP_DIR}/outputs" "${APP_DIR}/logs"

systemctl daemon-reload
systemctl enable okx-signal.service okx-signal-health.timer

sudo -u "${APP_USER}" "${VENV_DIR}/bin/python" "${APP_DIR}/scripts/runtime_check.py" preflight --env-file "${ENV_FILE}"

if [[ "${START_SERVICE}" == "true" ]]; then
  systemctl restart okx-signal.service
  systemctl start okx-signal-health.timer
  systemctl --no-pager --full status okx-signal.service
else
  echo "Installation complete. Service was not started."
  echo "Review ${ENV_FILE}, keep DEPLOYMENT_MODE=observation, then run:"
  echo "  systemctl start okx-signal.service okx-signal-health.timer"
fi
