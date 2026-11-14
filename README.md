# Scove

Integrazione Home Assistant per comandare un Roomba (serie i/s con Smart Map) senza dipendere da altre integrazioni: collegamento locale diretto al robot, programmi gestiti da Home Assistant, salute dei componenti, avvisi, pulizia per stanza e mappa dei passaggi.

## Installazione

1. Disinstalla Roomba+ (o l'integrazione iRobot ufficiale): il robot accetta **una sola** connessione locale alla volta.
2. Aggiungi questo repository come repository personalizzato e installa «Scove», poi riavvia Home Assistant.
3. Impostazioni → Dispositivi e servizi → Aggiungi integrazione → Scove. Servono email e password dell'account iRobot e l'indirizzo IP del robot.
4. Nell'app iRobot disattiva i programmi del robot, altrimenti partirà due volte.

Dopo l'installazione compare la voce **Scove** nella barra laterale.

## Cosa fa

- **Comandi**: avvia, pausa, stop, base, trova, svuota; pulizia di una o più stanze.
- **Programma**: più programmi con giorni, orario e stanze. Se il robot non può partire (già al lavoro, contenitore pieno, batteria bassa, non raggiungibile) il programma viene saltato con un avviso.
- **Salute**: punteggio generale, ore rimaste per contenitore della polvere, pulizia e sostituzione del filtro, spazzole, spazzola laterale, pulizia di ruote, contatti e sensori; stato della batteria; storico delle pulizie.
- **Avvisi**: partenza, errori, robot bloccato, contenitore pieno, manutenzione, programma saltato, robot non raggiungibile, fine pulizia. Arrivano come notifica di Home Assistant, come evento `scove_alert` e, se indicato, a un servizio `notify`.
- **Stanze e aree**: ogni stanza della mappa del robot si associa a un'area di Home Assistant; il servizio `scove.clean_rooms` accetta `rooms` oppure `areas`.
- **Scheda per le dashboard**: `type: custom:scove-map-card` mostra stanze, zone vietate e passaggi (opzioni `title`, `history`).
- **Mappa**: stanze del robot, zone vietate impostate in iRobot, percorso dell'ultima pulizia e storico dei passaggi.

## Limiti noti della 1.0.0

- **Zone vietate disegnate in Scove**: vengono salvate e mostrate, ma non ancora inviate al robot. Il robot rispetta solo le zone create nell'app iRobot. Il cloud iRobot non ha un'interfaccia pubblica per scriverle e nessun progetto aperto lo fa: servirà catturare il traffico dell'app iRobot mentre si salva una zona.
- **Contatori dei componenti**: partono dal momento dell'installazione. Premi «Fatto» quando sostituisci o pulisci un pezzo.
- **Percorso**: il robot viene interrogato una volta al secondo durante la pulizia. Se il percorso appare spostato o ruotato rispetto alle stanze, usa «Allineamento» nella scheda Mappa.
- **Programma interno del robot**: non viene modificato da Scove; va disattivato dall'app iRobot.

## Servizi

| Servizio | Dati |
| --- | --- |
| `scove.clean_rooms` | `rooms: [nomi]` oppure `areas: [area_id]` |
| `scove.reset_part` | `part: bin \| filter_clean \| filter \| brushes \| side_brush \| wheels \| contacts \| sensors` |
| `scove.refresh_map` | — |

Usa la libreria [roombapy](https://github.com/pschmitt/roombapy) (MIT) per il collegamento locale.
