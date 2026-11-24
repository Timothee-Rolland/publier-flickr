#!/bin/bash
# Construit dist/Publier-sur-Flickr.dmg : fenêtre « glisser l'app dans Applications ».
# macOS uniquement (hdiutil). Construit d'abord l'app autonome si besoin.
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(uname -s)" = Darwin ] || { echo "✗ macOS requis (hdiutil)"; exit 1; }
[ -d "dist/Publier sur Flickr.app" ] || scripts/construire_zip_autonome.sh
python3 -m pip install --quiet dmgbuild
# fond net sur écran Retina : TIFF double résolution
tiffutil -cathidpicheck mac/dmg/fond.png mac/dmg/fond@2x.png -out dist/fond.tiff
rm -f dist/Publier-sur-Flickr.dmg
python3 -m dmgbuild -s mac/dmg/reglages.py \
  -D app="dist/Publier sur Flickr.app" -D fond=dist/fond.tiff \
  "Publier sur Flickr" dist/Publier-sur-Flickr.dmg
rm -f dist/fond.tiff
echo "✓ dist/Publier-sur-Flickr.dmg ($(du -h dist/Publier-sur-Flickr.dmg | cut -f1))"
