"""Publier sur Flickr — mini-app (fenêtre native via pywebview)."""
import base64
import hashlib
import json
import os
import plistlib
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


PROTEGES = {"Desktop": "Bureau", "Documents": "Documents", "Downloads": "Téléchargements",
            "Pictures": "Images", "Movies": "Vidéos", "Music": "Musique"}
AGENT = Path.home() / "Library" / "LaunchAgents" / "fr.epy.publier-flickr.plist"


def chemin_lisible(p):
    p = Path(p)
    try:
        rel = p.relative_to(Path.home())
    except ValueError:
        return str(p)
    parts = list(rel.parts)
    if parts and parts[0] in PROTEGES:
        parts[0] = PROTEGES[parts[0]]
    return "~/" + "/".join(parts) if parts else "~"


def acces_dossier(p):
    """'ok', 'absent' ou 'bloque' (protection macOS des dossiers Bureau, Documents…)."""
    try:
        p.mkdir(parents=True, exist_ok=True)
        list(os.scandir(p))
        (p / core.SOUS_DOSSIER_PUBLIES).mkdir(exist_ok=True)
        return "ok"
    except PermissionError:
        return "bloque"
    except OSError:
        return "absent"


def chemin_app():
    """Chemin de l'app (.app) : fourni par le lanceur, sinon retrouvé par Spotlight."""
    if os.environ.get("PF_APP"):
        return os.environ["PF_APP"]
    for c in ("/Applications/Publier sur Flickr.app",
              str(Path.home() / "Applications" / "Publier sur Flickr.app")):
        if Path(c).exists():
            return c
    try:
        r = subprocess.run(["mdfind", "kMDItemCFBundleIdentifier == 'fr.epy.publier-flickr'"],
                           capture_output=True, text=True, timeout=5)
        for ligne in r.stdout.splitlines():
            if ligne.endswith(".app"):
                return ligne
    except Exception:
        pass
    return None


class Surveillant(threading.Thread):
    """Surveille le dossier choisi : chaque sous-dossier qui contient des photos/vidéos et dont
    la copie est terminée (rien n'a bougé depuis ~10 s) est proposé à l'import."""
    def __init__(self, api):
        super().__init__(daemon=True)
        self.api = api
        self.etats = {}       # chemin -> signature au passage précédent
        self.signales = set()
        self.reveil = threading.Event()

    def run(self):
        while True:
            try:
                self.tour()
            except Exception:
                traceback.print_exc()
            self.reveil.wait(5)
            self.reveil.clear()

    def tour(self):
        racine = core.dossier_surveille()
        acces = acces_dossier(racine)
        prets, en_copie = [], []
        if acces == "ok":
            for d in sorted(racine.iterdir(), key=lambda x: x.name.lower()):
                if not d.is_dir() or d.name.startswith(".") or d.name == core.SOUS_DOSSIER_PUBLIES:
                    continue
                try:
                    sig = core.signature(d)
                except OSError:
                    continue
                if sig[0] == 0:
                    continue
                stable = self.etats.get(str(d)) == sig and time.time() - sig[2] > 10
                self.etats[str(d)] = sig
                if not stable:
                    en_copie.append({"nom": d.name, "nb": sig[0]})
                elif not core.publie_entierement(d):
                    prets.append({"chemin": str(d), "nom": d.name, "nb": sig[0],
                                  "taille": sig[1], "nouveau": str(d) not in self.signales})
        nouveaux = [x for x in prets if x["nouveau"]]
        self.signales |= {x["chemin"] for x in prets}
        self.api._sur_surveillance({"dossier": str(racine), "lisible": chemin_lisible(racine),
                                    "acces": acces, "prets": prets, "en_copie": en_copie,
                                    "nouveaux": [x["nom"] for x in nouveaux]})


class Api:
    def __init__(self):
        self._fenetre = None
        self._flickr = None
        self._arret = False
        self._surveillant = None
        self._barre = None
        self._quitter = False
        self._attente = 0
        self._reouverture = False
        self._mode_dock = False   # vrai si l'icône de barre des menus est invisible
        self._discret = False

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
                self._js("surLimites", self._flickr.limites())
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

    # ------------------------------------------------ dossier surveillé
    def _sur_surveillance(self, etat):
        self._attente = len(etat["prets"])
        badge_et_barre(self, self._attente, bool(etat["nouveaux"]))
        self._js("surSurveillance", etat)

    def rafraichir_surveillance(self):
        if self._surveillant:
            self._surveillant.reveil.set()

    def reglages(self):
        d = core.dossier_surveille()
        return {"dossier": str(d), "lisible": chemin_lisible(d), "acces": acces_dossier(d),
                "demarrage": AGENT.exists(), "app": chemin_app() is not None,
                "barre": bool(self._barre and self._barre.visible())}

    def choisir_dossier_surveille(self):
        """Choix du dossier ; passer par ce sélecteur vaut aussi autorisation macOS pour la session."""
        actuel = core.dossier_surveille()
        res = self._fenetre.create_file_dialog(webview.FileDialog.FOLDER,
                                               directory=str(actuel.parent if actuel.parent.exists() else Path.home()))
        if res:
            choix = Path(res[0] if isinstance(res, (list, tuple)) else res)
            core.enregistrer_reglage("surveillance", "dossier", str(choix))
            self.rafraichir_surveillance()
        return self.reglages()

    def autoriser_session(self):
        """Ouvre le sélecteur sur le dossier surveillé : le valider débloque l'accès jusqu'à la fermeture."""
        d = core.dossier_surveille()
        res = self._fenetre.create_file_dialog(webview.FileDialog.FOLDER, directory=str(d.parent))
        if res:
            choix = Path(res[0] if isinstance(res, (list, tuple)) else res)
            if choix != d:
                core.enregistrer_reglage("surveillance", "dossier", str(choix))
            self.rafraichir_surveillance()
        return self.reglages()

    def ouvrir_reglages_acces(self):
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"])
        app = chemin_app()
        if app:  # montre l'app dans le Finder pour la glisser dans la liste
            subprocess.run(["open", "-R", app])

    def ouvrir_dossier_surveille(self):
        d = core.dossier_surveille()
        if acces_dossier(d) != "absent":
            subprocess.run(["open", str(d)])

    def definir_demarrage(self, actif):
        app = chemin_app()
        if actif and app:
            AGENT.parent.mkdir(parents=True, exist_ok=True)
            with open(AGENT, "wb") as f:
                plistlib.dump({"Label": "fr.epy.publier-flickr",
                               "ProgramArguments": [str(Path(app) / "Contents" / "MacOS" / "lanceur")],
                               "EnvironmentVariables": {"PF_DISCRET": "1", "PF_APP": app},
                               "RunAtLoad": True, "LimitLoadToSessionType": "Aqua"}, f)
        elif AGENT.exists():
            AGENT.unlink()
        core.enregistrer_reglage("surveillance", "demarrage", "1" if actif and app else "0")
        return self.reglages()

    # ------------------------------------------------ fenêtre
    def montrer(self, sous_icone=False):
        """Affiche la fenêtre au premier plan ; sous_icone : la place juste sous l'icône
        de la barre des menus, comme un menu."""
        def f():
            try:
                from AppKit import NSApplication, NSScreen
                from webview.platforms.cocoa import BrowserView
                i = BrowserView.instances.get(self._fenetre.uid) if self._fenetre else None
                if i is None:
                    return
                w = i.window
                if w.isMiniaturized():
                    w.deminiaturize_(None)
                cadre = self._barre.cadre_icone() if (sous_icone and self._barre) else None
                if cadre is not None:
                    ecran = (w.screen() or NSScreen.mainScreen()).visibleFrame()
                    larg = w.frame().size.width
                    x = cadre.origin.x + cadre.size.width - larg
                    x = max(ecran.origin.x + 8, min(x, ecran.origin.x + ecran.size.width - larg - 8))
                    w.setFrameTopLeftPoint_((x, ecran.origin.y + ecran.size.height - 4))
                w.makeKeyAndOrderFront_(None)
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                traceback.print_exc()
        appel_principal(f)

    def basculer_fenetre(self):
        """Clic sur l'icône de la barre des menus : ouvre la fenêtre, ou la masque si elle est devant."""
        try:
            from AppKit import NSApplication
            from webview.platforms.cocoa import BrowserView
            i = BrowserView.instances.get(self._fenetre.uid)
            if i is not None and i.window.isVisible() and NSApplication.sharedApplication().isActive():
                i.window.orderOut_(None)
                return
        except Exception:
            pass
        self.montrer(sous_icone=True)

    def quitter(self):
        self._quitter = True
        if self._fenetre:
            self._fenetre.destroy()

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
            nouvelle = self._ranger(racine, bilan, len(albums))
            self._js("surTermine", {"bilan": bilan, "racine": nouvelle})
            self.rafraichir_surveillance()
            try:
                self._js("surTags", self._flickr.tags_existants())
                self._js("surAlbumsExistants", self._flickr.albums_existants())
            except Exception:
                pass
        threading.Thread(target=tache, daemon=True).start()

    def _ranger(self, racine, bilan, n):
        """Dossier venant du dossier surveillé et entièrement publié -> rangé dans « Publiés »."""
        if not racine:
            return None
        r = Path(racine)
        surveille = core.dossier_surveille()
        if r.parent != surveille or len(bilan) != n or any(b.get("echecs") for b in bilan):
            return None
        if not core.publie_entierement(r):
            return None
        dest = surveille / core.SOUS_DOSSIER_PUBLIES / r.name
        k = 2
        while dest.exists():
            dest = surveille / core.SOUS_DOSSIER_PUBLIES / f"{r.name} ({k})"
            k += 1
        try:
            shutil.move(str(r), str(dest))
            return str(dest)
        except OSError:
            traceback.print_exc()
            return None

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
        appli.setActivationPolicy_(1)  # app de barre des menus : pas d'icône dans le Dock
        image = NSImage.alloc().initWithContentsOfFile_(str(ICI / "icone.png"))
        if image is not None:
            appli.setApplicationIconImage_(image)
    except Exception as e:  # hors macOS ou PyObjC indisponible : sans conséquence
        print("identité macOS :", e, flush=True)


def appel_principal(f):
    """Exécute f sur le fil principal (obligatoire pour AppKit)."""
    try:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(f)
    except Exception:
        pass


def activer_app():
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def badge_et_barre(api, n, signaler):
    def maj():
        try:
            from AppKit import NSApplication
            appli = NSApplication.sharedApplication()
            appli.dockTile().setBadgeLabel_(str(n) if n else "")
            if signaler:
                appli.requestUserAttention_(10)  # le Dock rebondit une fois
            if api._barre:
                api._barre.mettre_a_jour(n)
        except Exception:
            pass
    appel_principal(maj)


def creer_barre_menus(api):
    """App de barre des menus : clic sur l'icône = ouvrir/masquer la fenêtre juste dessous ;
    clic droit (ou ctrl-clic) = menu (albums en attente, dossier surveillé, Quitter)."""
    try:
        import objc
        from AppKit import (NSApplication, NSStatusBar, NSMenu, NSMenuItem, NSImage,
                            NSVariableStatusItemLength, NSEvent)
        from Foundation import NSObject, NSMakeSize

        class Barre(NSObject):
            def initAvecApi_(self, a):
                self = objc.super(Barre, self).init()
                self.api = a
                self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
                self.item.setAutosaveName_("fr.epy.publier-flickr")
                self.item.setVisible_(True)
                bouton = self.item.button()
                img = NSImage.alloc().initWithContentsOfFile_(str(ICI / "icone.png"))
                if img is not None:
                    img.setSize_(NSMakeSize(18, 18))
                    bouton.setImage_(img)
                else:
                    bouton.setTitle_("Flickr")
                bouton.setToolTip_("Publier sur Flickr")
                bouton.setTarget_(self)
                bouton.setAction_("clic:")
                bouton.sendActionOn_((1 << 2) | (1 << 4))  # clic gauche relâché | clic droit relâché
                self.menu = NSMenu.alloc().init()
                self.info = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Aucun album en attente", None, "")
                self.menu.addItem_(self.info)
                self.menu.addItem_(NSMenuItem.separatorItem())
                for titre, action, touche in (("Ouvrir Publier sur Flickr", "ouvrir:", ""),
                                              ("Ouvrir le dossier surveillé", "dossier:", ""),
                                              ("Quitter", "quitter:", "q")):
                    it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(titre, action, touche)
                    it.setTarget_(self)
                    self.menu.addItem_(it)
                return self

            def clic_(self, _):
                ev = NSApplication.sharedApplication().currentEvent()
                droit = ev is not None and (ev.type() == 4 or (ev.modifierFlags() & (1 << 18)))
                if droit:   # menu
                    self.item.setMenu_(self.menu)
                    self.item.button().performClick_(None)
                    self.item.setMenu_(None)
                else:       # fenêtre
                    self.api.basculer_fenetre()

            def cadre_icone(self):
                fen = self.item.button().window()
                return fen.frame() if fen is not None else None

            def visible(self):
                """Vrai si l'icône est réellement affichée (et pas cachée derrière l'encoche
                ou faute de place dans la barre des menus)."""
                try:
                    fen = self.item.button().window()
                    return fen is not None and bool(fen.occlusionState() & 2) \
                        and fen.frame().size.width > 0
                except Exception:
                    return False

            def mettre_a_jour(self, n):
                self.item.button().setTitle_(f" {n}" if n else "")
                self.info.setTitle_(f"{n} album{'s' if n > 1 else ''} en attente" if n else "Aucun album en attente")

            def ouvrir_(self, _):
                self.api.montrer(sous_icone=True)

            def dossier_(self, _):
                self.api.ouvrir_dossier_surveille()

            def quitter_(self, _):
                self.api.quitter()

        api._barre = Barre.alloc().initAvecApi_(api)

        def verifier():
            # Si l'icône n'est pas visible (encoche, barre pleine), l'app reste dans le Dock :
            # sinon elle serait introuvable une fois la fenêtre masquée.
            time.sleep(2)
            vis = api._barre.visible()
            print(f"barre des menus : créée, visible={vis}, cadre={api._barre.cadre_icone()}", flush=True)
            if not vis:
                api._mode_dock = True
                appel_principal(lambda: passer_dans_le_dock(api))
        threading.Thread(target=verifier, daemon=True).start()
    except Exception as e:
        print("barre des menus :", e, flush=True)
        api._mode_dock = True
        passer_dans_le_dock(api)
    try:
        import objc
        from webview.platforms.cocoa import BrowserView

        # PyObjC exige que la catégorie porte le nom de la classe qu'elle complète
        class AppDelegate(objc.Category(BrowserView.AppDelegate)):
            @objc.typedSelector(b"Z@:@Z")
            def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visibles):
                api.montrer()   # double-clic sur l'app dans le Finder/Launchpad alors qu'elle tourne
                return True

            @objc.typedSelector(b"Q@:@")
            def applicationShouldTerminate_(self, app):
                api._quitter = True      # Cmd+Q / Quitter : on quitte vraiment
                core.deverrouiller()
                return 1                 # NSTerminateNow

        api._reouverture = True
    except Exception as e:
        print("délégué d'application :", e, flush=True)
        api._reouverture = False


def passer_dans_le_dock(api):
    try:
        from AppKit import NSApplication, NSImage
        appli = NSApplication.sharedApplication()
        appli.setActivationPolicy_(0)
        image = NSImage.alloc().initWithContentsOfFile_(str(ICI / "icone.png"))
        if image is not None:
            appli.setApplicationIconImage_(image)
    except Exception:
        traceback.print_exc()


def au_demarrage(fenetre, api):
    fenetre.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
    fenetre.dom.document.events.drop += DOMEventHandler(api.sur_depot, True, True)
    appel_principal(lambda: creer_barre_menus(api))
    if not api._discret:  # premier affichage : sous l'icône de la barre des menus
        appel_principal(lambda: api.montrer(sous_icone=True))
    api._surveillant = Surveillant(api)
    api._surveillant.start()


def sur_fermeture(fenetre, api):
    """Cmd+W / bouton rouge : la fenêtre est masquée, la surveillance continue.
    Cmd+Q ou « Quitter » : on quitte vraiment (api._quitter)."""
    if api._quitter:
        return True

    def masquer():
        try:
            from webview.platforms.cocoa import BrowserView
            i = BrowserView.instances.get(fenetre.uid)
            if i is None:
                return
            if api._mode_dock and not api._reouverture:
                i.window.miniaturize_(None)     # secours : réduite dans le Dock, toujours récupérable
            else:
                i.window.orderOut_(None)
        except Exception:
            traceback.print_exc()
    appel_principal(masquer)
    return False


def main():
    identite_macos()
    core.deverrouiller()  # au cas où un envoi précédent aurait été interrompu brutalement
    threading.Thread(target=nettoyer_cache, daemon=True).start()
    api = Api()
    discret = os.environ.get("PF_DISCRET") == "1" or "--discret" in sys.argv
    fenetre = webview.create_window(
        "Publier sur Flickr", url=str(ICI / "ui.html"), js_api=api,
        width=1040, height=760, min_size=(820, 560), hidden=discret)
    api._fenetre = fenetre
    api._discret = discret
    fenetre.events.closing += lambda: sur_fermeture(fenetre, api)
    webview.start(au_demarrage, (fenetre, api))


if __name__ == "__main__":
    main()
