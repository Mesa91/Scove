"""Costanti di Scove."""
from __future__ import annotations

DOMAIN = "scove"
VERSION = "1.0.8"
PLATFORMS = ["vacuum", "sensor", "binary_sensor", "button"]

CONF_BLID = "blid"
CONF_ROBOT_PASSWORD = "robot_password"
CONF_COUNTRY = "country"

SIGNAL_UPDATE = "scove_update_{}"
STORAGE_VERSION = 1

# Ore di lavoro tra una manutenzione e l'altra (valori iRobot per la serie i).
PARTS: dict[str, dict] = {
    "bin": {"name": "Contenitore della polvere", "hours": 3, "icon": "mdi:delete-variant",
            "todo": "Svuota il contenitore della polvere", "routine": True},
    "filter_clean": {"name": "Pulizia filtro", "hours": 12, "icon": "mdi:air-filter",
                     "todo": "Pulisci il filtro", "routine": True},
    "filter": {"name": "Filtro", "hours": 52, "icon": "mdi:air-filter", "todo": "Sostituisci il filtro"},
    "brushes": {"name": "Spazzole", "hours": 312, "icon": "mdi:brush", "todo": "Sostituisci le spazzole"},
    "side_brush": {"name": "Spazzola laterale", "hours": 157, "icon": "mdi:fan",
                   "todo": "Sostituisci la spazzola laterale"},
    "wheels": {"name": "Pulizia ruote", "hours": 60, "icon": "mdi:tire", "todo": "Pulisci le ruote"},
    "contacts": {"name": "Pulizia contatti di ricarica", "hours": 60, "icon": "mdi:flash",
                 "todo": "Pulisci i contatti di ricarica"},
    "sensors": {"name": "Pulizia sensori", "hours": 30, "icon": "mdi:eye",
                "todo": "Pulisci i sensori di dislivello"},
}
# Componenti di versioni precedenti, da togliere dal registro delle entità.
REMOVED_PARTS = ("bag",)

MISSION_PHASES = {"run", "hmUsrDock", "hmMidMsn", "hmPostMsn", "evac", "pause", "stuck"}
MOVING_PHASES = {"run", "hmUsrDock", "hmMidMsn", "hmPostMsn"}

PHASE_LABELS = {
    "charge": "In carica", "run": "Sta pulendo", "stop": "Fermo", "pause": "In pausa",
    "stuck": "Bloccato", "hmUsrDock": "Torna alla base", "hmMidMsn": "Torna a ricaricarsi",
    "hmPostMsn": "Rientro a fine pulizia", "evac": "Svuotamento contenitore",
    "new": "Nuova missione", "": "Sconosciuto",
}

ERRORS = {
    1: "Ruota sinistra sollevata o bloccata", 2: "Spazzole principali bloccate",
    3: "Ruota destra sollevata o bloccata", 4: "Ruota sinistra bloccata",
    5: "Ruota destra bloccata", 6: "Sensori di dislivello sporchi o robot sul bordo",
    7: "Ruota sinistra bloccata", 8: "Aspirazione ostruita: pulisci il filtro",
    9: "Paraurti bloccato", 10: "Ruota destra bloccata", 11: "Motore di aspirazione",
    14: "Contenitore mancante o inserito male", 15: "Riavvio interno del robot",
    16: "Robot urtato o posato in pendenza", 17: "Percorso bloccato: non riesce a terminare",
    18: "Non riesce a rientrare alla base", 19: "Non riesce a sganciarsi dalla base",
    21: "Navigazione persa", 26: "Aspirazione ostruita", 31: "Errore interno",
    34: "Non riesce a liberarsi dagli ostacoli", 39: "Robot spostato a mano: posizione persa",
    46: "Batteria scarica", 47: "Riavvio interno", 48: "Percorso bloccato",
    68: "Mappa non aggiornata", 73: "Tasca del tampone", 74: "Mappa non caricabile",
    76: "Errore hardware", 88: "Navigazione non riuscita",
    216: "Il contenitore potrebbe essere pieno: svuotalo e pulisci il sensore",
    224: "Smart Map non riconosciuta: riallinea la mappa",
}
