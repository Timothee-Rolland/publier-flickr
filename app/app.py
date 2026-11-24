"""Publier sur Flickr — mini-app (fenêtre native via pywebview)."""
import json
import threading
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

    def publier(self, albums):
        self._arret = False

        def tache():
            bilan = []
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
            self._js("surTermine", bilan)
            try:
                self._js("surTags", self._flickr.tags_existants())
                self._js("surAlbumsExistants", self._flickr.albums_existants())
            except Exception:
                pass
        threading.Thread(target=tache, daemon=True).start()

    def ouvrir(self, url):
        import webbrowser
        webbrowser.open(url)

    def apercu(self, chemin):
        """Miniature en data-URL (les fichiers locaux ne sont pas accessibles directement)."""
        import base64
        try:
            from PIL import Image
            import io
            im = Image.open(chemin)
            im.thumbnail((240, 240))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=70)
            data = buf.getvalue()
        except Exception:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def identite_macos():
    """Nom et icône de l'app dans le Dock et la barre de menus (sinon : « Python »)."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Publier sur Flickr"
        image = NSImage.alloc().initWithContentsOfFile_(str(ICI / "icone.png"))
        if image is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception as e:  # hors macOS ou PyObjC indisponible : sans conséquence
        print("identité macOS :", e, flush=True)


def brancher_depot(fenetre, api):
    fenetre.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
    fenetre.dom.document.events.drop += DOMEventHandler(api.sur_depot, True, True)


def main():
    identite_macos()
    api = Api()
    fenetre = webview.create_window(
        "Publier sur Flickr", url=str(ICI / "ui.html"), js_api=api,
        width=1040, height=760, min_size=(820, 560))
    api._fenetre = fenetre
    webview.start(brancher_depot, (fenetre, api))


if __name__ == "__main__":
    main()
