# Réglages dmgbuild : fenêtre « glisser l'app dans Applications ».
# Utilisé par scripts/construire_dmg.sh (macOS uniquement).
import os

application = defines.get("app", "dist/Publier sur Flickr.app")  # noqa: F821 (fourni par dmgbuild)
nom_app = os.path.basename(application)

format = "UDZO"
filesystem = "HFS+"
files = [application]
symlinks = {"Applications": "/Applications"}
hide_extensions = [nom_app]
icon = os.path.join(application, "Contents", "Resources", "icone.icns")

background = defines.get("fond", "mac/dmg/fond.png")  # noqa: F821
window_rect = ((200, 140), (600, 400))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 112
text_size = 13
icon_locations = {nom_app: (150, 200), "Applications": (450, 200)}
