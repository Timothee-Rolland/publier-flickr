#!/bin/bash
# Construit l'app à distribuer : dist/Publier sur Flickr.app + dist/Publier sur Flickr.zip
# Usage : scripts/construire_app.sh [chemin/vers/config.ini]   (par défaut ./config.ini, jamais commité)
set -e
cd "$(dirname "$0")/.."
CONFIG="${1:-config.ini}"
if [ ! -f "$CONFIG" ] || grep -q COLLER "$CONFIG"; then
  echo "✗ Il faut un config.ini contenant la clé Flickr (copier config.exemple.ini)."; exit 1
fi
APP="dist/Publier sur Flickr.app"
rm -rf dist && mkdir -p dist
cp -R "mac/Publier sur Flickr.app" "$APP"
while read -r f; do [ -n "$f" ] && cp "app/$f" "$APP/Contents/Resources/"; done < app/fichiers.txt
cp app/fichiers.txt "$APP/Contents/Resources/"
cp "$CONFIG" "$APP/Contents/Resources/config.ini"
V="$(cat app/VERSION)"
sed -i.bak "s#<string>[0-9.]*</string><!--v-->#<string>$V</string><!--v-->#g" "$APP/Contents/Info.plist" && rm -f "$APP/Contents/Info.plist.bak"
chmod 755 "$APP/Contents/MacOS/lanceur"
cp docs/Installation.txt dist/
(cd dist && zip -qry "Publier sur Flickr.zip" "Publier sur Flickr.app" Installation.txt)
echo "✓ dist/Publier sur Flickr.zip (version $V) — contient la clé : à partager en interne uniquement."
