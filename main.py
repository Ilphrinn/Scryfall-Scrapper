# =============================================================================
#  POINT D'ENTRÉE DU PROGRAMME
# =============================================================================
# Ce fichier est le premier fichier exécuté quand on lance l'application.
# Son seul rôle est d'importer la fonction principale (main) depuis le dossier
# du programme, puis de l'appeler.
#
# POUR LANCER LE PROGRAMME :
#   python main.py
# =============================================================================

from scryfall_art_downloader.app import main   # Importe la fonction qui lance l'interface graphique


if __name__ == "__main__":
    # Ce bloc ne s'exécute QUE si on lance ce fichier directement.
    # Si un autre fichier importe main.py, ce bloc est ignoré.
    main()
