#!/usr/bin/env bash
# Install (or remove) the desktop entry for the current user.
# Nothing is copied out of this directory — the launcher points back at it,
# so you can keep the checkout wherever you like and `git pull` in place.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${HOME}/.local/bin"
APPS="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-${HOME}/.local/share}/icons/hicolor"

uninstall() {
    rm -f "${BIN}/lnx-isocam" \
          "${APPS}/lnx-isocam.desktop" \
          "${ICONS}/256x256/apps/lnx-isocam.png" \
          "${ICONS}/128x128/apps/lnx-isocam.png"
    echo "removed."
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
    command -v update-desktop-database >/dev/null && update-desktop-database -q "${APPS}" || true
    exit 0
fi

mkdir -p "${BIN}" "${APPS}" "${ICONS}/256x256/apps" "${ICONS}/128x128/apps"

# launcher — resolves to wherever this checkout lives
cat > "${BIN}/lnx-isocam" <<EOF
#!/usr/bin/env bash
exec python3 "${SRC}/app.py" "\$@"
EOF
chmod +x "${BIN}/lnx-isocam"

install -m 644 "${SRC}/lnx-isocam.desktop"       "${APPS}/lnx-isocam.desktop"
install -m 644 "${SRC}/icons/lnx-isocam.png"     "${ICONS}/256x256/apps/lnx-isocam.png"
install -m 644 "${SRC}/icons/lnx-isocam-128.png" "${ICONS}/128x128/apps/lnx-isocam.png"

command -v update-desktop-database >/dev/null && update-desktop-database -q "${APPS}" || true
command -v gtk-update-icon-cache   >/dev/null && gtk-update-icon-cache -qtf "${ICONS}" 2>/dev/null || true

echo "installed:"
echo "  launcher      ${BIN}/lnx-isocam"
echo "  desktop entry ${APPS}/lnx-isocam.desktop"
echo "  icons         ${ICONS}/{256x256,128x128}/apps/lnx-isocam.png"
echo
case ":${PATH}:" in
    *":${BIN}:"*) ;;
    *) echo "note: ${BIN} is not in your PATH — the menu entry works regardless," ;
       echo "      but 'lnx-isocam' on the command line will not until you add it." ;;
esac
