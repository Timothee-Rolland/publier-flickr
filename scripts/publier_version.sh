#!/bin/bash
# Publie une nouvelle version : les apps installées la récupèrent au lancement suivant.
# Usage : scripts/publier_version.sh 2.2.0 "Ce qui change"
set -e
cd "$(dirname "$0")/.."
V="$1"; NOTES="${2:-Nouvelle version}"
[[ "$V" =~ ^[0-9]+(\.[0-9]+)*$ ]] || { echo "Usage : $0 2.2.0 \"notes\""; exit 1; }
scripts/verifier_secrets.sh
git diff --quiet && git diff --cached --quiet || { echo "✗ Des modifications ne sont pas commitées."; exit 1; }
echo "$V" > app/VERSION
git add app/VERSION && git commit -m "Version $V"
git tag "v$V"
git push && git push origin "v$V"
if command -v gh >/dev/null; then
  gh release create "v$V" --title "Version $V" --notes "$NOTES"
else
  echo "Créez la release sur : https://github.com/Timothee-Rolland/publier-flickr/releases/new?tag=v$V"
fi
