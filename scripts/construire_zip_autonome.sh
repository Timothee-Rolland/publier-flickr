#!/bin/bash
# Construit l'app AUTONOME : Python et tous les composants sont embarqués (Apple Silicon + Intel).
# L'utilisateur télécharge le zip, le décompresse, double-clique : rien à installer.
#   scripts/construire_zip_autonome.sh   -> dist/Publier sur Flickr.zip (sans clé)
# Prérequis (machine de construction, macOS ou Linux) : bash, python3 + pip, uv (pip install uv), zip.
set -euo pipefail
cd "$(dirname "$0")/.."
RACINE="$PWD"
PYV="3.12"
TRAVAIL="$(mktemp -d)"; trap 'rm -rf "$TRAVAIL"' EXIT
APP="dist/Publier sur Flickr.app"

paquets_binaires=(flickrapi requests requests-oauthlib requests-toolbelt oauthlib urllib3 idna certifi
  charset-normalizer six pillow pywebview bottle typing_extensions pyobjc-core pyobjc-framework-Cocoa
  pyobjc-framework-Quartz pyobjc-framework-WebKit pyobjc-framework-Security
  pyobjc-framework-UniformTypeIdentifiers)

construire_runtime() {  # $1 = arm64 | x86_64
  local arch="$1" uvarch plateformes dest sp
  if [ "$arch" = arm64 ]; then
    uvarch=aarch64
    plateformes=(macosx_11_0_arm64 macosx_11_0_universal2 macosx_10_13_universal2 macosx_10_9_universal2)
  else
    uvarch=x86_64
    plateformes=(macosx_11_0_x86_64 macosx_10_13_x86_64 macosx_10_10_x86_64 macosx_10_9_x86_64
                 macosx_10_13_universal2 macosx_10_9_universal2)
  fi
  echo "→ Python $PYV ($arch)"
  UV_PYTHON_INSTALL_DIR="$TRAVAIL/py" uv python install --quiet "cpython-$PYV-macos-$uvarch-none"
  dest="$APP/Contents/Resources/runtime/$arch"
  mkdir -p "$dest"
  cp -R "$(find "$TRAVAIL/py" -maxdepth 1 -type d -name "cpython-$PYV.*-macos-$uvarch-none" | head -1)/." "$dest/"
  sp="$dest/lib/python$PYV/site-packages"
  local args=(); for p in "${plateformes[@]}"; do args+=(--platform "$p"); done
  # --no-deps + liste explicite : les dépendances propres à macOS ne seraient pas résolues depuis Linux
  python3 -m pip install --quiet --disable-pip-version-check --only-binary=:all: "${args[@]}" \
    --python-version "$PYV" --implementation cp --target "$sp" --no-deps "${paquets_binaires[@]}"
  python3 -m pip install --quiet --disable-pip-version-check --target "$sp" --no-deps proxy_tools
  # allègement
  local L="$dest/lib/python$PYV"
  rm -rf "${dest:?}/include" "${dest:?}/share" "${L:?}/test" "${L:?}/idlelib" "${L:?}/tkinter" \
         "${L:?}/turtledemo" "${L:?}/ensurepip" "${L:?}/lib2to3" "${L:?}/unittest/test" \
         "${sp:?}/PyObjCTest" "${sp:?}/bin"
  rm -rf "$dest"/lib/tcl* "$dest"/lib/tk* "$dest"/lib/itcl* "$dest"/lib/thread* "$dest"/lib/tdbc* \
         "$dest"/lib/libtcl* "$dest"/lib/libtk* "$L"/lib-dynload/_tkinter* "$L"/config-*
  find "$dest" -name __pycache__ -type d -prune -exec rm -rf {} +
  find "$dest" -name "*.a" -delete
}

rm -rf dist && mkdir -p dist
cp -R "mac/Publier sur Flickr.app" "$APP"
while read -r f; do [ -n "$f" ] && cp "app/$f" "$APP/Contents/Resources/"; done < app/fichiers.txt
cp app/fichiers.txt "$APP/Contents/Resources/"
cp config.exemple.ini "$APP/Contents/Resources/config.ini"
V="$(cat app/VERSION)"
sed -i.bak "s#<string>[0-9.]*</string><!--v-->#<string>$V</string><!--v-->#g" "$APP/Contents/Info.plist"
rm -f "$APP/Contents/Info.plist.bak"
construire_runtime arm64
construire_runtime x86_64
echo "$PYV-$V" > "$APP/Contents/Resources/runtime/VERSION"
chmod 755 "$APP/Contents/MacOS/lanceur"
cp docs/Installation.txt dist/
(cd dist && zip -qry "Publier sur Flickr.zip" "Publier sur Flickr.app" Installation.txt)
echo "✓ dist/Publier sur Flickr.zip — version $V, $(du -h "dist/Publier sur Flickr.zip" | cut -f1), sans clé"
