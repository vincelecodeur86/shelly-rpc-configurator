#!/bin/sh
# Crée dist/shelly-configurator-<version>-linux.tar.gz à copier sur le poste Linux cible,
# où l'on exécute ensuite :  tar xzf shelly-configurator-*.tar.gz && cd shelly-configurator-* && ./installer/linux/install.sh
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/shelly_configurator/__init__.py")
NAME=shelly-configurator-$VERSION
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$NAME/installer" "$ROOT/dist"
cp -R "$ROOT/shelly_configurator" "$ROOT/tests" "$ROOT/README.md" "$TMP/$NAME/"
cp -R "$ROOT/installer/linux" "$TMP/$NAME/installer/"
find "$TMP/$NAME" -name '__pycache__' -type d -prune -exec rm -rf {} +
chmod 755 "$TMP/$NAME/installer/linux/"*.sh
tar -C "$TMP" -czf "$ROOT/dist/$NAME-linux.tar.gz" "$NAME"
echo "Archive créée : $ROOT/dist/$NAME-linux.tar.gz"
