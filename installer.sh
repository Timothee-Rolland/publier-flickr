#!/bin/bash
# Installation de « Publier sur Flickr » en une commande :
#   curl -fsSL https://raw.githubusercontent.com/Timothee-Rolland/publier-flickr/main/installer.sh | bash
#
# - installe l'app dans Applications (pas d'avertissement « développeur non identifié »,
#   car les fichiers téléchargés par curl ne sont pas mis en quarantaine) ;
# - installe Python et les composants sans les outils de développement Apple ;
# - relancer la commande réinstalle / met à jour l'app sans toucher aux réglages.
set -euo pipefail

DEPOT="Timothee-Rolland/publier-flickr"
NOM="Publier sur Flickr"
SUP="$HOME/Library/Application Support/$NOM"

etape() { printf '\n\033[1m%s\033[0m\n' "$1"; }
echec() { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || echec "Cette installation est réservée à macOS."
majeur="$(sw_vers -productVersion | cut -d. -f1)"
[ "$majeur" -ge 11 ] || echec "macOS 11 (Big Sur) ou plus récent est nécessaire."

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

etape "1/4 · Téléchargement de l'app"
TAG="$(curl -fsSL "https://api.github.com/repos/$DEPOT/releases/latest" \
  | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[ -n "$TAG" ] || echec "Impossible de joindre GitHub. Vérifiez la connexion internet."
curl -fsSL "https://codeload.github.com/$DEPOT/tar.gz/refs/tags/$TAG" | tar -xz -C "$TMP"
SRC="$(find "$TMP" -maxdepth 1 -type d -name 'publier-flickr-*' | head -1)"
[ -d "$SRC/app" ] || echec "Archive incomplète."
echo "   version ${TAG#v}"

etape "2/4 · Installation dans Applications"
DEST="/Applications"; [ -w "$DEST" ] || { DEST="$HOME/Applications"; mkdir -p "$DEST"; }
APP="$DEST/$NOM.app"
osascript -e "tell application \"$NOM\" to quit" >/dev/null 2>&1 || true
rm -rf "$APP"
cp -R "$SRC/mac/$NOM.app" "$APP"
while read -r f; do
  [ -n "$f" ] && cp "$SRC/app/$f" "$APP/Contents/Resources/"
done < "$SRC/app/fichiers.txt"
cp "$SRC/app/fichiers.txt" "$APP/Contents/Resources/"
cp "$SRC/config.exemple.ini" "$APP/Contents/Resources/config.ini"   # sans clé : demandée au 1er lancement
chmod 755 "$APP/Contents/MacOS/lanceur"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
echo "   $APP"

etape "3/4 · Python (quelques minutes la première fois)"
mkdir -p "$SUP"
PY="$SUP/.venv/bin/python3"
if ! "$PY" -c "import sys" >/dev/null 2>&1; then
  case "$(uname -m)" in arm64) ARCH="aarch64";; *) ARCH="x86_64";; esac
  curl -fsSL "https://github.com/astral-sh/uv/releases/latest/download/uv-$ARCH-apple-darwin.tar.gz" \
    | tar -xz -C "$TMP"
  UV="$(find "$TMP" -type f -name uv | head -1)"
  [ -x "$UV" ] || echec "Téléchargement de Python impossible."
  rm -rf "$SUP/.venv"
  UV_PYTHON_INSTALL_DIR="$SUP/python" UV_PYTHON_PREFERENCE=only-managed "$UV" venv --quiet --seed --python 3.12 "$SUP/.venv"
fi
echo "   $("$PY" --version)"

etape "4/4 · Composants"
"$PY" -m pip install --quiet --disable-pip-version-check --upgrade pip -r "$SRC/app/requirements.txt"
echo "   flickrapi, pywebview, pillow"

# Icône dans le Dock (si elle n'y est pas déjà) + rafraîchissement du cache d'icônes
touch "$APP"
URL="file://$(printf '%s' "$APP" | sed 's/ /%20/g')/"
if ! defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "$NOM\|Publier%20sur%20Flickr"; then
  defaults write com.apple.dock persistent-apps -array-add \
    "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>$URL</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>"
  killall Dock 2>/dev/null || true
  echo "   Icône ajoutée au Dock"
fi

printf '\n\033[32m✓ Installé.\033[0m Ouverture de l'"'"'app…\n'
printf '  Au premier lancement : coller la clé et le secret Flickr, puis autoriser l'"'"'accès dans le navigateur.\n\n'
open "$APP"
