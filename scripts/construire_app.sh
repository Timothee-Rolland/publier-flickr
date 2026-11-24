#!/bin/bash
# Construit l'app à distribuer : dist/Publier sur Flickr.app + dist/Publier sur Flickr.zip
#   scripts/construire_app.sh            -> sans clé : l'app la demande au premier lancement (recommandé)
#   scripts/construire_app.sh config.ini -> clé intégrée : zip à garder strictement en interne
set -e
cd "$(dirname "$0")/.."
CONFIG="${1:-config.exemple.ini}"
[ -f "$CONFIG" ] || { echo "✗ Fichier introuvable : $CONFIG"; exit 1; }
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
if grep -q COLLER "$APP/Contents/Resources/config.ini"; then
  echo "✓ dist/Publier sur Flickr.zip (version $V) — sans clé : demandée au premier lancement."
else
  echo "⚠ dist/Publier sur Flickr.zip (version $V) — CONTIENT LA CLÉ : à partager en interne uniquement."
fi
