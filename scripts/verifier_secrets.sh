#!/bin/bash
# Refuse toute clé Flickr dans les fichiers suivis par git (ou indexés pour commit).
cd "$(git rev-parse --show-toplevel)" || exit 1
fichiers="$( { git ls-files; git diff --cached --name-only; } | sort -u)"
probleme=0
if echo "$fichiers" | grep -qE '(^|/)config\.ini$'; then
  echo "✗ config.ini est suivi par git : il contient les clés, retirez-le (git rm --cached)."; probleme=1
fi
for f in $fichiers; do
  [ -f "$f" ] || continue
  if grep -qEi 'api_(key|secret)[[:space:]]*=[[:space:]]*[0-9a-f]{12,}' "$f"; then
    echo "✗ Clé ou secret Flickr détecté dans $f"; probleme=1
  fi
done
[ $probleme = 0 ] && echo "✓ Aucun secret dans le dépôt."
exit $probleme
