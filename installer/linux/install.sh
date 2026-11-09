#!/bin/sh
# Installation de Shelly Configurator sous Linux.
#   sudo ./install.sh   -> pour tous les utilisateurs (/opt/shelly-configurator, /usr/local/bin)
#   ./install.sh        -> pour l'utilisateur courant (~/.local/share/shelly-configurator, ~/.local/bin)
# Prérequis : Python 3.7+ avec Tkinter (paquet python3-tk / python3-tkinter), installé si possible.
set -eu

APP=shelly-configurator
NAME="Shelly Configurator"
SRC_ROOT=$(cd "$(dirname "$0")/../.." && pwd)

if [ ! -d "$SRC_ROOT/shelly_configurator" ]; then
    echo "Erreur : dossier shelly_configurator introuvable dans $SRC_ROOT" >&2
    exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
    PREFIX=/opt/$APP
    BIN_DIR=/usr/local/bin
    DESKTOP_DIR=/usr/share/applications
else
    PREFIX=${XDG_DATA_HOME:-$HOME/.local/share}/$APP/app
    BIN_DIR=$HOME/.local/bin
    DESKTOP_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/applications
fi

# --- Python -----------------------------------------------------------------------------------
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    echo "Erreur : python3 est introuvable. Installez Python 3.7 ou plus récent." >&2
    exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)'; then
    echo "Erreur : Python 3.7 minimum requis ($("$PYTHON" --version 2>&1) trouvé)." >&2
    exit 1
fi

# --- Tkinter ----------------------------------------------------------------------------------
if ! "$PYTHON" -c 'import tkinter' 2>/dev/null; then
    echo "Tkinter est absent."
    if [ "$(id -u)" -eq 0 ]; then
        echo "Installation du paquet Tkinter..."
        if command -v apt-get >/dev/null 2>&1; then apt-get install -y python3-tk
        elif command -v dnf >/dev/null 2>&1; then dnf install -y python3-tkinter
        elif command -v yum >/dev/null 2>&1; then yum install -y python3-tkinter
        elif command -v zypper >/dev/null 2>&1; then zypper --non-interactive install python3-tk
        elif command -v pacman >/dev/null 2>&1; then pacman -S --noconfirm tk
        else echo "Gestionnaire de paquets non reconnu : installez Tkinter manuellement." >&2; exit 1
        fi
    else
        echo "Installez-le puis relancez ce script, par exemple :" >&2
        echo "  Debian/Ubuntu : sudo apt-get install python3-tk" >&2
        echo "  Fedora/RHEL   : sudo dnf install python3-tkinter" >&2
        echo "  (ou lancez ce script avec sudo pour une installation automatique)" >&2
        exit 1
    fi
    "$PYTHON" -c 'import tkinter' || { echo "Erreur : Tkinter toujours indisponible." >&2; exit 1; }
fi

# --- Copie des fichiers -----------------------------------------------------------------------
echo "Installation dans $PREFIX"
rm -rf "$PREFIX/shelly_configurator"
mkdir -p "$PREFIX" "$BIN_DIR" "$DESKTOP_DIR"
cp -R "$SRC_ROOT/shelly_configurator" "$PREFIX/"
[ -f "$SRC_ROOT/README.md" ] && cp "$SRC_ROOT/README.md" "$PREFIX/"
find "$PREFIX" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
chmod -R a+rX "$PREFIX"

LAUNCHER=$BIN_DIR/$APP
cat > "$LAUNCHER" <<EOF
#!/bin/sh
PYTHONPATH="$PREFIX\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON" -m shelly_configurator "\$@"
EOF
chmod 755 "$LAUNCHER"

cat > "$DESKTOP_DIR/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$NAME
Comment=Configuration automatisée des Shelly Plus 1PM
Exec=$LAUNCHER
Icon=preferences-system-network
Terminal=false
Categories=Network;Utility;
EOF
chmod 644 "$DESKTOP_DIR/$APP.desktop"

cat > "$PREFIX/uninstall.sh" <<EOF
#!/bin/sh
# Désinstallation de $NAME (la configuration et les journaux de chaque utilisateur sont conservés :
# ~/.config/$APP et ~/.local/share/$APP)
set -e
rm -f "$LAUNCHER" "$DESKTOP_DIR/$APP.desktop"
rm -rf "$PREFIX"
echo "$NAME désinstallé."
EOF
chmod 755 "$PREFIX/uninstall.sh"

echo
echo "$NAME installé."
echo "  Lancement : $LAUNCHER   (ou via le menu des applications)"
echo "  Désinstallation : $PREFIX/uninstall.sh"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "  Remarque : $BIN_DIR n'est pas dans votre PATH." ;;
esac
