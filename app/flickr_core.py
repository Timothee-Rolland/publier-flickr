"""Logique Flickr partagée : connexion, tags, albums, analyse de dossiers, publication."""
import configparser
import json
import os
import re
import stat
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import flickrapi

ICI = Path(__file__).resolve().parent
CONFIG = ICI / "config.ini"
IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".heic", ".heif"}
VIDEOS = {".mp4", ".mov", ".m4v", ".avi", ".wmv", ".mpg", ".mpeg", ".3gp",
          ".mts", ".m2ts", ".ogv"}  # formats acceptés par Flickr
EXTENSIONS = IMAGES | VIDEOS
SUIVI = ".flickr_suivi.json"


# ---------------------------------------------------------------- configuration
def lire_config():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG, encoding="utf-8")
    return cfg


def cles_ok(cfg):
    cle = cfg.get("flickr", "api_key", fallback="").strip()
    sec = cfg.get("flickr", "api_secret", fallback="").strip()
    return bool(cle and sec and "COLLER" not in cle and "COLLER" not in sec)


def tester_cle(cle, secret):
    """Vérifie la clé auprès de Flickr (le secret, lui, est vérifié à la connexion)."""
    try:
        flickrapi.FlickrAPI(cle.strip(), secret.strip(), format="parsed-json",
                            store_token=False).reflection.getMethods()
        return None
    except flickrapi.exceptions.FlickrError as e:
        return "Clé refusée par Flickr. Vérifiez qu'elle est copiée en entier." \
            if "100" in str(e) or "key" in str(e).lower() else f"Flickr : {e}"
    except Exception as e:
        return f"Flickr injoignable ({e}). Vérifiez la connexion internet."


def enregistrer_cles(cle, secret):
    cfg = lire_config()
    if not cfg.has_section("flickr"):
        cfg.add_section("flickr")
    cfg.set("flickr", "api_key", cle.strip())
    cfg.set("flickr", "api_secret", secret.strip())
    with open(CONFIG, "w", encoding="utf-8") as f:
        cfg.write(f)
    CONFIG.chmod(0o600)  # lisible par l'utilisateur seulement


def tags_par_defaut(cfg):
    txt = cfg.get("defaut", "tags", fallback="")
    return [t.strip() for t in txt.split(",") if t.strip()]


def public_par_defaut(cfg):
    return cfg.get("defaut", "public", fallback="0").strip() == "1"


# ---------------------------------------------------------------- dossier surveillé
DOSSIER_SURVEILLE_DEFAUT = "~/À publier sur Flickr"
SOUS_DOSSIER_PUBLIES = "Publiés"


def enregistrer_reglage(section, cle, valeur):
    cfg = lire_config()
    if not cfg.has_section(section):
        cfg.add_section(section)
    cfg.set(section, cle, str(valeur))
    with open(CONFIG, "w", encoding="utf-8") as f:
        cfg.write(f)
    CONFIG.chmod(0o600)


def dossier_surveille(cfg=None):
    cfg = cfg or lire_config()
    return Path(cfg.get("surveillance", "dossier", fallback=DOSSIER_SURVEILLE_DEFAUT)).expanduser()


def demarrage_auto(cfg=None):
    cfg = cfg or lire_config()
    return cfg.get("surveillance", "demarrage", fallback="0").strip() == "1"


def signature(dossier):
    """(nombre de fichiers, taille totale, dernière modification) des médias du dossier."""
    n = taille = 0
    recent = 0.0
    for f in dossier.rglob("*"):
        if any(part.startswith(".") for part in f.relative_to(dossier).parts):
            continue
        if f.is_file() and f.suffix.lower() in EXTENSIONS:
            st = f.stat()
            n += 1
            taille += st.st_size
            recent = max(recent, st.st_mtime)
    return n, taille, recent


def publie_entierement(dossier):
    """Vrai si tous les médias de tous les albums du dossier sont déjà sur Flickr."""
    for d in [dossier] + [x for x in dossier.rglob("*") if x.is_dir()]:
        ph = photos_de(d)
        if ph:
            deja = lire_suivi(d).get("photos", {})
            if any(p.name not in deja for p in ph):
                return False
    return True


# ---------------------------------------------------------------- tags
def normaliser(tag):
    """Forme « propre » comme Flickr : minuscules, sans espaces ni ponctuation."""
    return "".join(c for c in tag.lower() if c.isalnum())


def sans_accents(texte):
    return "".join(c for c in unicodedata.normalize("NFD", texte)
                   if unicodedata.category(c) != "Mn")


def formater_tags(tags):
    """['EPY', 'Philippe Benassaya'] -> 'EPY "Philippe Benassaya"' (format Flickr)."""
    vus, sortie = set(), []
    for t in tags:
        t = t.strip().replace('"', "")
        if t and normaliser(t) not in vus:
            vus.add(normaliser(t))
            sortie.append(f'"{t}"' if " " in t else t)
    return " ".join(sortie)


# ---------------------------------------------------------------- géocodage
def geocoder(commune):
    url = ("https://api-adresse.data.gouv.fr/search/?" +
           urllib.parse.urlencode({"q": commune, "type": "municipality", "limit": 1}))
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            f = json.load(r)["features"][0]
        lon, lat = f["geometry"]["coordinates"]
        return lat, lon, f["properties"].get("label", commune)
    except Exception:
        return None


def taille_lisible(octets):
    for unite in ("o", "Ko", "Mo", "Go", "To"):
        if octets < 1024 or unite == "To":
            if unite in ("o", "Ko"):
                return f"{octets:.0f} {unite}"
            return f"{octets:.1f}".rstrip("0").rstrip(".").replace(".", ",") + f" {unite}"
        octets /= 1024


# ---------------------------------------------------------------- communes
def chercher_communes(texte, limite=8):
    """Recherche dans le référentiel officiel des communes (geo.api.gouv.fr).
    Les communes des Yvelines passent en premier."""
    texte = texte.strip()
    if len(texte) < 2:
        return []
    url = ("https://geo.api.gouv.fr/communes?" + urllib.parse.urlencode({
        "nom": texte, "fields": "nom,code,codeDepartement,codesPostaux,centre",
        "boost": "population", "limit": 30}))
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            donnees = json.load(r)
    except Exception:
        return None  # hors ligne
    donnees.sort(key=lambda c: c.get("codeDepartement") != "78")  # tri stable
    res = []
    for c in donnees[:limite]:
        lon, lat = (c.get("centre") or {}).get("coordinates", [None, None])
        res.append({"nom": c["nom"], "code": c["code"], "dep": c.get("codeDepartement", ""),
                    "cp": (c.get("codesPostaux") or [""])[0], "lat": lat, "lon": lon})
    return res


# ---------------------------------------------------------------- dates
DATE_TITRE = re.compile(r"\s+-\s+(\d{2})\.(\d{2})\.(\d{2})$")


def titre_avec_date(titre, date_iso):
    """« Théâtre » + « 2026-11-20 » -> « Théâtre - 20.11.26 »."""
    titre = titre.strip()
    if not date_iso:
        return titre
    a, m, j = date_iso.split("-")
    return f"{titre} - {j}.{m}.{a[2:]}"


def decouper_titre(titre):
    """« Théâtre - 20.11.26 » -> (« Théâtre », « 2026-11-20 »)."""
    m = DATE_TITRE.search(titre or "")
    if not m:
        return (titre or "").strip(), ""
    j, mo, a = m.groups()
    return titre[:m.start()].strip(), f"20{a}-{mo}-{j}"


def date_prise_de_vue(photos):
    """Date EXIF de la première photo qui en a une (« AAAA-MM-JJ »), sinon ""."""
    try:
        from PIL import Image
    except ImportError:
        return ""
    for p in [x for x in photos if x.suffix.lower() in IMAGES][:10]:
        try:
            exif = Image.open(p).getexif()
            brut = exif.get_ifd(0x8769).get(36867) or exif.get(306)
            if brut and re.match(r"\d{4}:\d{2}:\d{2}", str(brut)):
                return str(brut)[:10].replace(":", "-")
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------- verrouillage
# Pendant un envoi, le dossier et ses fichiers sont verrouillés (comme « Verrouillé »
# dans Lire les informations du Finder) : le Finder refuse de les supprimer sans
# confirmation. Ils sont déverrouillés à la fin, même en cas d'erreur.
VERROU = getattr(stat, "UF_IMMUTABLE", 0)
FICHIER_VERROUS = ICI / "verrous.json"


def verrouiller(chemins):
    """Verrouille les chemins (fichiers d'abord, dossiers ensuite) ; renvoie ceux verrouillés
    par l'app (ceux déjà verrouillés par l'utilisateur ne sont pas touchés)."""
    if not VERROU or not hasattr(os, "chflags"):
        return []
    faits = []
    for c in chemins:
        try:
            fl = os.lstat(c).st_flags
            if fl & VERROU:
                continue
            os.chflags(c, fl | VERROU)
            faits.append(str(c))
        except OSError:
            pass
    FICHIER_VERROUS.write_text(json.dumps(faits, ensure_ascii=False))
    return faits


def deverrouiller(chemins=None):
    """Déverrouille ; sans argument, reprend une liste laissée par un arrêt brutal."""
    if chemins is None:
        try:
            chemins = json.loads(FICHIER_VERROUS.read_text())
        except Exception:
            return
    if hasattr(os, "chflags") and VERROU:
        for c in reversed(chemins):
            try:
                os.chflags(c, os.lstat(c).st_flags & ~VERROU)
            except OSError:
                pass
    try:
        FICHIER_VERROUS.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- dossiers
def photos_de(dossier):
    return sorted(p for p in dossier.iterdir()
                  if p.is_file() and p.suffix.lower() in EXTENSIONS
                  and not p.name.startswith("."))


def lire_suivi(dossier):
    f = dossier / SUIVI
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def analyser(chemin):
    """Un album par dossier contenant des photos (le dossier lui-même et ses sous-dossiers)."""
    racine = Path(chemin)
    if racine.is_file():
        racine = racine.parent
    dossiers = [racine] + sorted(
        (d for d in racine.rglob("*") if d.is_dir()
         and not any(part.startswith(".") for part in d.relative_to(racine).parts)),
        key=lambda d: str(d).lower())
    albums = []
    for d in dossiers:
        ph = photos_de(d)
        if not ph:
            continue
        suivi = lire_suivi(d)
        deja = suivi.get("photos", {})
        titre, date_titre = decouper_titre(suivi.get("titre", d.name))
        commune = suivi.get("commune_info")  # {nom, code, dep, cp, lat, lon}
        albums.append({
            "chemin": str(d),
            "relatif": "." if d == racine else str(d.relative_to(racine)),
            "titre": titre,
            "date": suivi.get("date") or date_titre or date_prise_de_vue(ph),
            "nb": len(ph),
            "deja": sum(1 for p in ph if p.name in deja),
            "tags": [t.strip() for t in suivi.get("tags", "").split(",") if t.strip()]
                    if suivi.get("tags") else [],
            "description": suivi.get("description", ""),
            "commune": commune if isinstance(commune, dict) else None,
            "public": suivi.get("public"),
            "album_id": suivi.get("album_id"),
            "apercu": [str(p) for p in ph[:4]],
            "fichiers": [{"nom": p.name, "chemin": str(p),
                          "video": p.suffix.lower() in VIDEOS,
                          "taille": p.stat().st_size,
                          "deja": p.name in deja} for p in ph],
        })
    return {"racine": str(racine), "nom": racine.name, "albums": albums}


# ---------------------------------------------------------------- Flickr
class Flickr:
    def __init__(self):
        cfg = lire_config()
        self.api = flickrapi.FlickrAPI(cfg.get("flickr", "api_key").strip(),
                                       cfg.get("flickr", "api_secret").strip(),
                                       format="parsed-json")
        self.moi = None

    def connecte(self):
        return self.api.token_valid(perms="write")

    def connecter(self):
        if not self.connecte():
            self.api.authenticate_via_browser(perms="write")
        u = self.api.test.login()["user"]
        self.moi = {"id": u["id"], "nom": u["username"]["_content"]}
        return self.moi

    def tags_existants(self):
        """[{'tag': 'Philippe Benassaya', 'cle': 'philippebenassaya', 'nb': 12}, ...]"""
        tags = {}
        rep = self.api.tags.getListUserRaw()
        for t in rep["who"]["tags"]["tag"]:
            clean = str(t["clean"])  # Flickr renvoie parfois des nombres (tags « 2026 »)
            raws = t.get("raw") or [{"_content": clean}]
            tags[clean] = {"tag": str(raws[0]["_content"]), "cle": clean, "nb": 0}
        try:
            pop = self.api.tags.getListUserPopular(count=500)
            for t in pop["who"]["tags"]["tag"]:
                k = str(t["_content"])
                if k in tags:
                    tags[k]["nb"] = int(t.get("count", 0))
        except flickrapi.exceptions.FlickrError:
            pass
        return sorted(tags.values(), key=lambda t: (-t["nb"], t["tag"].lower()))

    # Limites par défaut de Flickr (aide Flickr) si le compte ne les communique pas
    LIMITES_DEFAUT = {"photo": 200 * 1024**2, "video": 1024**3, "duree": 0}

    def limites(self):
        """Taille maximale par photo et par vidéo (octets) et durée lue (s) pour ce compte."""
        if getattr(self, "_limites", None):
            return self._limites
        lim = dict(self.LIMITES_DEFAUT)
        try:
            p = self.api.people.getLimits()["person"]
            lim["photo"] = int(p["photos"]["maxupload"]) or lim["photo"]
            lim["video"] = int(p["videos"]["maxupload"]) or lim["video"]
            lim["duree"] = int(p["videos"].get("maxduration", 0) or 0)
        except Exception:
            pass
        self._limites = lim
        return lim

    def trop_lourd(self, chemin):
        p = Path(chemin)
        lim = self.limites()
        max_ = lim["video"] if p.suffix.lower() in VIDEOS else lim["photo"]
        return p.stat().st_size > max_, max_

    def albums_existants(self):
        albums, page = [], 1
        while True:
            rep = self.api.photosets.getList(page=page, per_page=500)["photosets"]
            for a in rep["photoset"]:
                albums.append({"id": str(a["id"]), "titre": str(a["title"]["_content"])})
            if page >= int(rep.get("pages", 1)):
                return albums
            page += 1

    # ---------------------------------------------------- publication
    def _envoyer(self, fichier, **params):
        for essai in range(3):
            try:
                rep = self.api.upload(filename=str(fichier), format="rest", **params)
                rep = rep.decode() if isinstance(rep, bytes) else rep
                racine = ET.fromstring(rep)
                if racine.get("stat") != "ok":
                    raise RuntimeError(rep)
                return racine.find("photoid").text
            except Exception as e:
                if essai == 2:
                    raise e
                time.sleep(3)

    def album_existe(self, set_id):
        try:
            self.api.photosets.getInfo(photoset_id=set_id)
            return True
        except flickrapi.exceptions.FlickrError:
            return False

    def photo_existe(self, pid):
        try:
            self.api.photos.getInfo(photo_id=pid)
            return True
        except flickrapi.exceptions.FlickrError:
            return False

    def _a_un_lieu(self, pid):
        try:
            self.api.photos.geo.getLocation(photo_id=pid)
            return True
        except flickrapi.exceptions.FlickrError:
            return False

    def publier_album(self, album, progres, arret):
        """album : dict(chemin, titre, tags[list], description, commune, public, cible)
        cible : "nouveau" pour créer un album, sinon l'id d'un album Flickr existant.
        progres(evenement: dict) est appelé à chaque étape ; arret() -> True pour stopper."""
        dossier = Path(album["chemin"])
        suivi = lire_suivi(dossier)
        envoyees = suivi.get("photos", {})
        titre_seul = album["titre"].strip() or dossier.name
        date_iso = album.get("date") or ""
        titre = titre_avec_date(titre_seul, date_iso)  # titre affiché sur Flickr
        description = album.get("description", "")
        est_public = bool(album.get("public"))
        public = "1" if est_public else "0"
        tags = formater_tags(album.get("tags", []))
        commune = album.get("commune") or None
        if isinstance(commune, dict) and commune.get("lat") is not None:
            lieu = (commune["lat"], commune["lon"], commune["nom"])
        elif isinstance(commune, str) and commune.strip():  # ancien format
            lieu = geocoder(commune.strip())
        else:
            lieu = None

        cible = str(album.get("cible") or suivi.get("album_id") or "nouveau")
        set_id = None
        if cible != "nouveau":
            if self.album_existe(cible):
                set_id = cible
            else:
                progres({"type": "info", "msg": "L'album n'existe plus sur Flickr : "
                                                "un nouvel album va être créé."})
        ancien_set = suivi.get("album_id")
        # album « externe » = album Flickr choisi dans la liste : on ne le renomme jamais
        externe = set_id is not None and (set_id != ancien_set or suivi.get("album_externe", False))
        suivi["album_id"] = set_id
        suivi["album_externe"] = externe
        changement = "public" in suivi and suivi["public"] != est_public
        suivi.update(titre=titre, date=date_iso, tags=", ".join(album.get("tags", [])),
                     description=description, public=est_public,
                     commune_info=commune if isinstance(commune, dict) else None)

        def sauver():
            suivi["photos"] = envoyees
            (dossier / SUIVI).write_text(json.dumps(suivi, ensure_ascii=False, indent=2),
                                         encoding="utf-8")

        if changement and envoyees:
            progres({"type": "info", "msg": f"Passage des photos déjà en ligne en "
                                            f"{'public' if est_public else 'privé'}…"})
            for pid in envoyees.values():
                try:
                    self.api.photos.setPerms(photo_id=pid, is_public=public,
                                             is_friend="0", is_family="0")
                except flickrapi.exceptions.FlickrError:
                    pass

        # Album changé (supprimé sur Flickr, nouvel album, album existant choisi) :
        # on y remet les photos déjà envoyées ; celles supprimées de Flickr seront renvoyées.
        if envoyees and (set_id is None or set_id != ancien_set):
            progres({"type": "info", "msg": "Vérification des photos déjà envoyées…"})
            for nom, pid in list(envoyees.items()):
                if arret():
                    break
                if not self.photo_existe(pid):
                    del envoyees[nom]
                    continue
                if set_id is None:
                    set_id = self.api.photosets.create(title=titre, description=description,
                                                       primary_photo_id=pid)["photoset"]["id"]
                    suivi["album_id"] = set_id
                else:
                    try:
                        self.api.photosets.addPhoto(photoset_id=set_id, photo_id=pid)
                    except flickrapi.exceptions.FlickrError:
                        pass  # déjà dans l'album
            sauver()

        photos = photos_de(dossier)
        exclus = set(album.get("exclus") or [])  # décochés dans la galerie
        a_envoyer = [p for p in photos if p.name not in envoyees and p.name not in exclus]
        echecs = []
        for i, p in enumerate(a_envoyer, 1):
            if arret():
                progres({"type": "info", "msg": "Arrêté."})
                break
            progres({"type": "photo", "i": i, "n": len(a_envoyer), "fichier": p.name})
            lourd, max_ = self.trop_lourd(p)
            if lourd:  # Flickr le refuserait : inutile de l'envoyer
                echecs.append(p.name)
                progres({"type": "erreur", "msg": f"{p.name} : trop lourd pour Flickr "
                         f"({taille_lisible(p.stat().st_size)}, maximum {taille_lisible(max_)})"})
                continue
            try:
                pid = self._envoyer(p, title=titre, description=description, tags=tags,
                                    is_public=public, is_friend="0", is_family="0")
            except Exception as e:
                echecs.append(p.name)
                progres({"type": "erreur", "msg": f"{p.name} : {e}"})
                continue
            envoyees[p.name] = pid
            if not set_id:
                set_id = self.api.photosets.create(title=titre, description=description,
                                                   primary_photo_id=pid)["photoset"]["id"]
                suivi["album_id"] = set_id
            else:
                try:
                    self.api.photosets.addPhoto(photoset_id=set_id, photo_id=pid)
                except flickrapi.exceptions.FlickrError as e:
                    if "already" not in str(e).lower():
                        progres({"type": "erreur", "msg": f"{p.name} : ajout à l'album : {e}"})
            if lieu and not self._a_un_lieu(pid):
                try:
                    self.api.photos.geo.setLocation(photo_id=pid, lat=lieu[0],
                                                    lon=lieu[1], accuracy=11)
                except flickrapi.exceptions.FlickrError:
                    pass
            sauver()

        if set_id and not externe:
            try:
                self.api.photosets.editMeta(photoset_id=set_id, title=titre,
                                            description=description)
            except flickrapi.exceptions.FlickrError:
                pass
        sauver()
        url = (f"https://www.flickr.com/photos/{self.moi['id']}/albums/{set_id}"
               if set_id and self.moi else None)
        return {"envoyees": len(a_envoyer) - len(echecs), "echecs": echecs, "url": url}
