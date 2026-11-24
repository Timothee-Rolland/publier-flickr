"""Publier sur Flickr — mini-app (fenêtre native via pywebview)."""
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

import flickr_core as core

ICI = Path(__file__).resolve().parent


class Api:
    def __init__(self):
        self._fenetre = None
        self._flickr = None
        self._arret = False

    # ------------------------------------------------ outils
    def _js(self, fonction, donnees):
        if self._fenetre:
            self._fenetre.evaluate_js(f"{fonction}({json.dumps(donnees, ensure_ascii=False)})")

    def journal(self, message):
        print("[ui]", message, flush=True)

    # ------------------------------------------------ état / connexion
    def etat(self):
        cfg = core.lire_config()
        try:
            version = (core.ICI / "VERSION").read_text().strip()
        except OSError:
            version = ""
        return {"cles": core.cles_ok(cfg), "version": version,
                "cle": cfg.get("flickr", "api_key", fallback="") if core.cles_ok(cfg) else "",
                "tags_defaut": core.tags_par_defaut(cfg),
                "public_defaut": core.public_par_defaut(cfg)}

    def enregistrer_cles(self, cle, secret):
        if not secret.strip():  # modification : secret inchangé
            secret = core.lire_config().get("flickr", "api_secret", fallback="")
        erreur = core.tester_cle(cle, secret)
        if erreur:
            return {"ok": False, "erreur": erreur}
        core.enregistrer_cles(cle, secret)
        self._flickr = None
        return {"ok": True, "etat": self.etat()}

    def connecter(self):
        def tache():
            try:
                self._flickr = core.Flickr()
                moi = self._flickr.connecter()
                self._js("surConnexion", {"ok": True, "moi": moi})
                self._js("surTags", self._flickr.tags_existants())
                self._js("surAlbumsExistants", self._flickr.albums_existants())
            except Exception as e:
                traceback.print_exc()
                self._js("surConnexion", {"ok": False, "erreur": str(e)})
        threading.Thread(target=tache, daemon=True).start()

    def recharger_tags(self):
        if self._flickr:
            threading.Thread(target=lambda: self._js(
                "surTags", self._flickr.tags_existants()), daemon=True).start()

    # ------------------------------------------------ dossiers
    def choisir_dossier(self):
        res = self._fenetre.create_file_dialog(webview.FileDialog.FOLDER)
        if res:
            return core.analyser(res[0] if isinstance(res, (list, tuple)) else res)
        return None

    def analyser(self, chemin):
        return core.analyser(chemin)

    def sur_depot(self, evenement):
        fichiers = evenement.get("dataTransfer", {}).get("files", [])
        chemins = [f.get("pywebviewFullPath") for f in fichiers if f.get("pywebviewFullPath")]
        if not chemins:
            self._js("surErreur", "Impossible de lire le dossier déposé. "
                                  "Utilisez le bouton « Choisir un dossier ».")
            return
        self._js("surAnalyse", core.analyser(chemins[0]))

    # ------------------------------------------------ publication
    def arreter(self):
        self._arret = True

    def chercher_communes(self, texte):
        return core.chercher_communes(texte)

    def _a_verrouiller(self, albums, racine):
        """Fichiers des albums, puis leurs dossiers et les dossiers parents jusqu'à la racine."""
        fichiers, dossiers = [], set()
        racine = Path(racine) if racine else None
        for album in albums:
            d = Path(album["chemin"])
            suivi = d / core.SUIVI
            if not suivi.exists():          # créé avant verrouillage (dossier verrouillé =
                suivi.write_text("{}")      # plus de création de fichier possible)
            fichiers += [str(f) for f in core.photos_de(d)]
            while True:
                dossiers.add(d)
                if racine is None or d == racine or racine not in d.parents:
                    break
                d = d.parent
        ordre = sorted(dossiers, key=lambda x: len(x.parts), reverse=True)  # plus profonds d'abord
        return fichiers + [str(x) for x in ordre]

    def publier(self, albums, racine=None):
        self._arret = False

        def tache():
            bilan = []
            try:
                verrous = core.verrouiller(self._a_verrouiller(albums, racine))
            except Exception:
                traceback.print_exc()
                verrous = []
            try:
                self._publier(albums, bilan)
            finally:
                core.deverrouiller(verrous)
            self._js("surTermine", bilan)
            try:
                self._js("surTags", self._flickr.tags_existants())
                self._js("surAlbumsExistants", self._flickr.albums_existants())
            except Exception:
                pass
        threading.Thread(target=tache, daemon=True).start()

    def _publier(self, albums, bilan):
            for idx, album in enumerate(albums):
                if self._arret:
                    break
                self._js("surProgres", {"album": idx, "type": "debut"})
                try:
                    res = self._flickr.publier_album(
                        album,
                        lambda ev, idx=idx: self._js("surProgres", {**ev, "album": idx}),
                        lambda: self._arret)
                except Exception as e:
                    traceback.print_exc()
                    res = {"envoyees": 0, "echecs": ["(album)"], "url": None,
                           "erreur": str(e)}
                res["titre"] = album["titre"]
                bilan.append(res)
                self._js("surProgres", {"album": idx, "type": "fin", **res})

    def ouvrir_fichier(self, chemin):
        """Ouvre une photo ou une vidéo dans l'app par défaut de macOS (Aperçu, QuickTime…)."""
        f = Path(chemin)
        if f.is_file() and f.suffix.lower() in core.EXTENSIONS:
            subprocess.run(["open", str(f)])

    def ouvrir(self, url):
        import webbrowser
        webbrowser.open(url)

    def apercu(self, chemin, taille=320):
        """Miniature (data-URL) d'une photo ou d'une vidéo, mise en cache."""
        f = miniature(Path(chemin), int(taille))
        if not f:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()


CACHE = ICI / "cache"


def miniature(p, taille):
    """Fichier JPEG miniature en cache ; Pillow pour les photos, Coup d'œil (qlmanage)
    pour les vidéos, le HEIC et le reste."""
    try:
        st = p.stat()
    except OSError:
        return None
    cle = hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}|{taille}".encode()).hexdigest()
    sortie = CACHE / f"{cle}.jpg"
    if sortie.exists():
        return sortie
    CACHE.mkdir(exist_ok=True)
    from PIL import Image, ImageOps
    if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff"}:
        try:
            im = Image.open(p)
            im.draft("RGB", (taille * 2, taille * 2))  # décodage JPEG réduit : rapide
            im = ImageOps.exif_transpose(im)
            im.thumbnail((taille, taille))
            im.convert("RGB").save(sortie, "JPEG", quality=75)
            return sortie
        except Exception:
            pass
    if sys.platform == "darwin":
        tmp = Path(tempfile.mkdtemp())
        try:
            subprocess.run(["qlmanage", "-t", "-s", str(taille), "-o", str(tmp), str(p)],
                           capture_output=True, timeout=30)
            png = next(tmp.glob("*.png"), None)
            if png:
                Image.open(png).convert("RGB").save(sortie, "JPEG", quality=75)
                return sortie
        except Exception:
            pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return None


def nettoyer_cache(jours=30):
    limite = time.time() - jours * 86400
    for f in CACHE.glob("*.jpg") if CACHE.exists() else []:
        try:
            if f.stat().st_atime < limite:
                f.unlink()
        except OSError:
            pass


def identite_macos():
    """Nom et icône de l'app dans le Dock et la barre de menus (sinon : « Python »)."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Publier sur Flickr"
        appli = NSApplication.sharedApplication()
        appli.setActivationPolicy_(0)  # app « normale » : Dock, menu, premier plan
        image = NSImage.alloc().initWithContentsOfFile_(str(ICI / "icone.png"))
        if image is not None:
            appli.setApplicationIconImage_(image)
    except Exception as e:  # hors macOS ou PyObjC indisponible : sans conséquence
        print("identité macOS :", e, flush=True)


def brancher_depot(fenetre, api):
    fenetre.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
    fenetre.dom.document.events.drop += DOMEventHandler(api.sur_depot, True, True)


def main():
    identite_macos()
    core.deverrouiller()  # au cas où un envoi précédent aurait été interrompu brutalement
    threading.Thread(target=nettoyer_cache, daemon=True).start()
    api = Api()
    fenetre = webview.create_window(
        "Publier sur Flickr", url=str(ICI / "ui.html"), js_api=api,
        width=1040, height=760, min_size=(820, 560))
    api._fenetre = fenetre
    webview.start(brancher_depot, (fenetre, api))


if __name__ == "__main__":
    main()
