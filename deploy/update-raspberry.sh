#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Uso: $0 <ip-raspberry>" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
ip=$1
if [[ ! $ip =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
  echo "Error: indica una dirección IPv4." >&2
  exit 2
fi

for command in rsync ssh; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Error: se requiere '$command' en el equipo local." >&2
    exit 1
  fi
done

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
target="pi@$ip"
remote_dir=/opt/test-bench-rollers

echo "Sincronizando la aplicación con $target:$remote_dir…"
rsync -az \
  "$repo_root/pyproject.toml" \
  "$repo_root/uv.lock" \
  "$repo_root/README.md" \
  "$target:$remote_dir/"
rsync -az --delete --delete-excluded \
  --exclude='__pycache__/' --exclude='*.py[co]' \
  "$repo_root/src/test_bench_rollers/" \
  "$target:$remote_dir/src/test_bench_rollers/"
rsync -az \
  "$repo_root/packages/mesh-schemas/pyproject.toml" \
  "$repo_root/packages/mesh-schemas/README.md" \
  "$target:$remote_dir/packages/mesh-schemas/"
rsync -az --delete --delete-excluded \
  --exclude='__pycache__/' --exclude='*.py[co]' \
  "$repo_root/packages/mesh-schemas/src/mesh_schemas/" \
  "$target:$remote_dir/packages/mesh-schemas/src/mesh_schemas/"

echo "Actualizando dependencias y reiniciando test-bench-rollers.service…"
ssh -t "$target" \
  "cd '$remote_dir' && /usr/local/bin/uv sync --frozen && sudo systemctl restart test-bench-rollers.service && sudo systemctl is-active --quiet test-bench-rollers.service && echo 'Servicio activo.'"
