#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VERSION="${1:-$(node -p 'require("./package.json").version')}"
NAME="notify-mcp-v${VERSION}"
DIST="$ROOT/dist"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
rm -rf "$DIST"
mkdir -p "$DIST" "$TMP/$NAME"
cp -a README*.md LICENSE package.json bin mcp npm docs deploy assets skill "$TMP/$NAME/"
find "$TMP/$NAME" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$TMP/$NAME" -name '*.pyc' -delete
cat > "$TMP/$NAME/install.sh" <<'INSTALL'
#!/usr/bin/env bash
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/share/notify}"
mkdir -p "$PREFIX"
cp -a . "$PREFIX/"
if command -v sudo >/dev/null 2>&1; then
  sudo install -m 0755 "$PREFIX/bin/notify" /usr/local/bin/notify
else
  install -m 0755 "$PREFIX/bin/notify" "$HOME/.local/bin/notify"
fi
cat <<EOF
Installed Notify MCP files to: $PREFIX

MCP command:
  /usr/bin/python3 $PREFIX/mcp/notify_mcp.py

npx-style local command after npm install:
  notify-mcp
EOF
INSTALL
chmod +x "$TMP/$NAME/install.sh"
(
  cd "$TMP"
  tar -czf "$DIST/$NAME.tar.gz" "$NAME"
  zip -qr "$DIST/$NAME.zip" "$NAME"
)
npm pack --pack-destination "$DIST" >/dev/null
( cd "$DIST" && sha256sum notify-mcp-* > SHA256SUMS )
ls -lh "$DIST"
