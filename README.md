# Publier sur Flickr

Mini-app macOS pour publier des dossiers de photos d'événements sur Flickr :
glisser-déposer d'un dossier, **un album par sous-dossier**, titre, description, commune
(localisation), **tags avec suggestion des tags déjà utilisés sur le compte**,
visibilité privée (par défaut) ou publique, ajout à un album existant, reprise après interruption.

## Structure

| Dossier | Contenu |
|---|---|
| `app/` | Le programme (Python + interface HTML). C'est ce que les apps installées téléchargent lors d'une mise à jour. |
| `mac/` | Le squelette de l'app macOS (lanceur, icône). |
| `scripts/` | Construction de l'app, publication d'une version, contrôle des secrets. |
| `docs/` | Notice d'installation jointe au zip. |

## ⚠️ Clé Flickr : jamais dans ce dépôt

La clé et le secret de l'application Flickr vont dans `config.ini`, **ignoré par git**.
Ils sont intégrés uniquement dans le zip distribué en interne.

À faire une fois après le clonage, pour que git bloque tout commit contenant une clé :

```bash
git config core.hooksPath scripts/hooks
```

## Construire l'app à distribuer

```bash
cp config.exemple.ini config.ini     # puis y coller la clé et le secret
scripts/construire_app.sh            # -> dist/Publier sur Flickr.zip
```

Le zip contient la clé : à partager en interne uniquement.

## Publier une mise à jour

```bash
# après avoir commité les modifications dans app/
scripts/publier_version.sh 2.2.0 "Ce qui change"
```

Le script contrôle l'absence de secret, crée le tag `v2.2.0` et la release GitHub.
Au lancement suivant, chaque app installée détecte la release, télécharge les fichiers
listés dans `app/fichiers.txt`, vérifie que le code Python est valide, met à jour les
composants si `requirements.txt` a changé, puis notifie l'utilisateur.
Sans connexion ou en cas d'échec, l'app démarre simplement avec la version déjà installée.

Seul le contenu de `app/` est mis à jour à distance. Une modification du lanceur
(`mac/…/lanceur`) nécessite de redistribuer le zip.

## Fonctionnement technique

- macOS interdit à une app non signée de lire le Bureau : le lanceur copie le programme
  dans `~/Library/Application Support/Publier sur Flickr` et l'exécute de là, dans un
  environnement Python dédié (`.venv`).
- Chaque dossier publié reçoit un fichier caché `.flickr_suivi.json` (photos déjà envoyées,
  album) qui permet de reprendre ou compléter un envoi sans doublon.
- Journal : `.journal.txt` à côté de l'app, ou `~/Library/Application Support/Publier sur Flickr/journal.txt`.
