# =============================================================================
#  ANALYSEUR DE DECKLIST — decklist_parser.py
# =============================================================================
# Ce module transforme un texte brut de decklist en objets Python structurés.
#
# Une "decklist" est une liste de cartes sous forme de texte, comme :
#
#     4 Lightning Bolt
#     2 Forest
#     1 Black Lotus
#     // Commentaire ignoré
#     20 Island
#
# Ce module lit ce texte ligne par ligne, extrait la quantité et le nom
# de chaque carte, et ignore les lignes mal formatées.
# =============================================================================

from __future__ import annotations

import re   # Module Python pour les "expressions régulières" (recherche de motifs dans du texte)

from .models import DecklistEntry   # Notre structure de données pour une ligne de decklist


# ---------------------------------------------------------------------------
#  Expression régulière pour analyser une ligne de decklist
# ---------------------------------------------------------------------------
# Une expression régulière (regex) est un motif de recherche dans du texte.
#
# Ce motif signifie :
#   ^\s*       — début de ligne, avec espaces optionnels
#   (\d+)      — UN OU PLUSIEURS chiffres (= la quantité) → groupe 1
#   \s+        — UN OU PLUSIEURS espaces séparateurs
#   (.+?)      — UN OU PLUSIEURS caractères quelconques (= le nom) → groupe 2
#   \s*$       — fin de ligne, avec espaces optionnels
#
# Exemples de lignes acceptées :
#   "4 Lightning Bolt"   → quantité=4, nom="Lightning Bolt"
#   "  1 Forest  "       → quantité=1, nom="Forest"
#
# Exemples de lignes REJETÉES :
#   "Forest"             → pas de quantité
#   "// Sideboard"       → commentaire
#   ""                   → ligne vide
DECKLIST_LINE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def parse_decklist(value: str) -> tuple[list[DecklistEntry], list[str]]:
    """
    Analyse un texte de decklist et retourne la liste des cartes trouvées.

    Lit le texte ligne par ligne. Pour chaque ligne :
    - Si elle correspond au format "QUANTITE NOM", on crée un DecklistEntry.
    - Sinon, on l'ajoute aux lignes ignorées.

    Les lignes vides sont silencieusement ignorées (pas d'erreur).

    Arguments :
        value (str) : Texte brut de la decklist, tel que collé dans l'interface.

    Retourne :
        tuple composé de :
            list[DecklistEntry] : Liste des entrées valides (cartes reconnues).
            list[str]           : Liste des lignes ignorées (format inconnu).

    Exemple :
        entries, skipped = parse_decklist("4 Lightning Bolt\\n// Sideboard\\n2 Forest")
        # entries  = [DecklistEntry(4, "Lightning Bolt"), DecklistEntry(2, "Forest")]
        # skipped  = ["// Sideboard"]
    """
    entries: list[DecklistEntry] = []   # Cartes valides trouvées
    skipped: list[str] = []             # Lignes que l'on n'a pas pu analyser

    for raw_line in value.splitlines():   # Parcourt chaque ligne du texte
        line = raw_line.strip()           # Supprime les espaces en début et fin

        if not line:
            # Ligne vide → on l'ignore silencieusement
            continue

        match = DECKLIST_LINE.match(line)
        if not match:
            # La ligne ne correspond pas au format attendu (ex: commentaire, section)
            skipped.append(raw_line)
            continue

        # Extraction des deux groupes capturés par l'expression régulière
        quantity = int(match.group(1))   # Groupe 1 : les chiffres → converti en entier
        name = match.group(2).strip()    # Groupe 2 : le nom de la carte

        if quantity <= 0 or not name:
            # Quantité nulle ou nom vide → ligne invalide
            skipped.append(raw_line)
            continue

        # Ligne valide → on crée et ajoute l'entrée
        entries.append(DecklistEntry(quantity=quantity, name=name))

    return entries, skipped
