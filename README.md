# Launchpad

Dashboard locale per scoprire servizi e porte, riconoscere processi e container Docker e gestire le sessioni tmux.

## Avvio

Richiede Linux, Python 3.10+ e il comando `ss` (pacchetto `iproute2`). `tmux` è facoltativo.

Per avviarlo manualmente: `python3 discovery.py`. L'interfaccia è disponibile su **http://localhost:7777**.

Per avviarlo automaticamente come servizio utente `systemd`, clona il repository in `~/projects/launchpad` e installa l'unità:

```bash
systemctl --user link "$(pwd)/systemd/launchpad.service"
systemctl --user enable --now launchpad.service
```

Se scegli un'altra cartella, aggiorna i percorsi nell'unità prima di installarla. Per controllare il servizio:

```bash
systemctl --user status launchpad.service
systemctl --user restart launchpad.service
journalctl --user -u launchpad.service -f
```

La configurazione è in `systemd/launchpad.service`. Per reinstallarla dopo uno spostamento del repository, aggiorna i percorsi nell'unità, collegala di nuovo ed esegui `systemctl --user daemon-reload && systemctl --user restart launchpad.service`. Se vuoi che il servizio utente resti attivo senza login, puoi abilitare il linger con `loginctl enable-linger "$USER"`.

Un worker del servizio scansiona porte, protocolli e sessioni tmux ogni 8 secondi, anche senza browser aperto. Il frontend riceve lo stato iniziale e i soli cambiamenti tramite Server-Sent Events (`/api/events`); non interroga le API a intervalli. **Aggiorna** chiede al worker una scansione immediata. Le modifiche ai file Python del backend riavviano automaticamente il processo; le modifiche a HTML, JavaScript e CSS ricaricano la pagina aperta. Puoi usare `python3 discovery.py --port 9000` per cambiare porta nell'avvio manuale.

Un indirizzo dedicato senza porta richiede un resolver e un proxy locali. Il proxy deve inoltrare le richieste con un `Host` locale accettato da Launchpad; l'interfaccia è pensata per l'accesso diretto a `localhost:7777`.

Launchpad va eseguito sullo stesso host e con lo stesso utente dei server e delle sessioni tmux. Il servizio web ascolta solo su `127.0.0.1` per impostazione predefinita. Per ogni porta TCP in ascolto invia una breve richiesta `HEAD /` sull'interfaccia locale. Se la porta parla HTTP, legge la risposta della pagina principale: una pagina HTML valida è classificata come **Web app**, una risposta JSON o un endpoint senza pagina come **API**. Le pagine di documentazione Swagger, ReDoc e OpenAPI vengono classificate come API. Se la risposta non è leggibile, mostra **HTTP**; le altre porte sono **TCP**. La classificazione è indicativa e si basa sulla pagina principale, senza esplorare tutti i percorsi del servizio. Le web app compaiono per prime per impostazione predefinita; puoi filtrare per tipo, cercare e cambiare ordinamento.

L'interfaccia accetta solo connessioni e intestazioni `Host` locali. I comandi tmux richiedono un token della sessione web e un'origine coerente con l'host. Non esporre il dashboard su Internet: mostra metadati dei processi locali e può eseguire gli script presenti in `~/tmux`.

Per le pagine HTML mostra anche titolo e favicon, quando disponibili. Nei link, `127.0.0.1` viene presentato come `localhost`. I processi di altri utenti possono comparire senza nome o sessione, secondo i permessi del sistema.

Per i servizi TCP non HTTP prova una breve richiesta di protocollo sulle porte note o quando il nome del processo o l'immagine Docker lo suggerisce. Riconosce PostgreSQL, MongoDB, Redis, MySQL/MariaDB e Memcached; SSH può essere identificato dal nome del processo. La card distingue tra **protocollo verificato** da una risposta, **riconosciuto dal processo**, **probabile dall'immagine Docker** e **probabile dalla porta**. La porta da sola è solo un indizio: un'applicazione può ascoltare su una porta normalmente usata da un database.

Quando il socket Docker locale è accessibile, Launchpad legge le porte TCP pubblicate e il nome, l'immagine, il progetto Compose e l'ora di avvio dei container. Mostra anche la mappatura porta host → porta interna. Non invia comandi di modifica a Docker. Se più container pubblicano la stessa porta su IP differenti, ciascuno compare nella propria card. Per i processi normali, l'ora di avvio del PID arriva da `/proc`; il browser calcola da lì l'uptime e ne aggiorna il testo ogni minuto senza interrogare il server. Per i container senza PID del servizio visibile, l'uptime indicato è quello del container. Il PID riportato dal daemon Docker non viene mostrato perché può riferirsi a un namespace o host diverso.

In WSL, Launchpad rileva automaticamente anche le porte TCP Windows tramite `powershell.exe`, mostrando **Windows**, nome del processo, PID e ora di avvio quando disponibili. Il rilevamento Windows legge l’elenco delle porte senza inviare richieste ai servizi. Le verifiche HTTP/HTTPS e la lettura di titolo e favicon tramite `curl.exe` vengono eseguite solo sulle porte indicate esplicitamente in `LAUNCHPAD_WINDOWS_HTTP_PORTS` (numeri separati da virgole, ad esempio `5178,3000`). Questa limitazione evita di contattare automaticamente servizi come SpaceDesk, che possono mostrare richieste di connessione. Le altre porte Windows compaiono come TCP con i dati del processo. I link locali sono pensati per un browser Windows. Le porte Windows e Linux rimangono distinte anche quando hanno lo stesso numero; i PID Windows non vengono associati a processi o sessioni tmux Linux. Il rilevamento richiede l'interoperabilità WSL con Windows: gli eseguibili vengono cercati nel PATH e in `/mnt/c/Windows/System32`, anche quando `appendWindowsPath=false`. Gli errori di scansione Windows compaiono nella dashboard senza interrompere il rilevamento Linux. Per i servizi TCP Windows, il protocollo è dedotto dal processo o dalla porta senza verifiche di protocollo dal lato Linux.

## Tray Windows con backend WSL

Il tray apre la dashboard nel browser Windows su `http://127.0.0.1:7777`, con doppio clic o con **Apri Launchpad** nel menu del tasto destro. Usa IPv4 esplicitamente, come il backend, per evitare timeout quando Windows risolve `localhost` su IPv6. L'icona arancione indica che la dashboard è raggiungibile; quella grigia indica che è offline. La verifica usa `/api/health` ogni 8 secondi, senza richiedere una scansione dei servizi.

Richiede Windows PowerShell 5.1 e il servizio utente `launchpad.service` già installato nella distribuzione WSL come descritto sopra. Non richiede pacchetti aggiuntivi o privilegi amministrativi. Dalla root del repository in WSL:

```bash
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
  -NoProfile -ExecutionPolicy Bypass \
  -File "$(wslpath -w "$PWD/tray/install.ps1")" \
  -Distribution "$WSL_DISTRO_NAME" -WslUser "$USER"
```

L'installer copia l'utility in `%LOCALAPPDATA%\Launchpad`, crea un collegamento nel menu Start e uno nella cartella Esecuzione automatica, poi avvia il tray. La copia locale permette l'avvio anche quando WSL non è ancora in esecuzione. All'avvio il tray chiede a WSL di avviare il servizio con `systemctl --user start launchpad.service`; non apre automaticamente il browser. Distribuzione e utente sono espliciti per usare le stesse sessioni tmux del backend.

Il menu comprende **Avvia servizio**, **Riavvia servizio**, **Avvia il tray al login** e **Esci dal tray**. Uscire chiude solo l'icona: il backend resta attivo. Un errore di avvio o riavvio viene mostrato con una notifica. Una seconda apertura non crea un'altra icona. L'avvio al login dell'icona Windows e l'abilitazione del servizio systemd sono indipendenti.

Per aggiornare il tray, chiudilo dal menu e riesegui l'installer. `-NoStart` installa senza avviarlo. Se il backend usa una porta diversa, aggiungi `-Port 9000`; l'opzione configura l'URL del tray, mentre la porta del backend va configurata separatamente nell'unità systemd.

Per rimuovere i collegamenti e chiudere il tray, lasciando attivo il servizio:

```bash
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
  -NoProfile -ExecutionPolicy Bypass \
  -File "$(wslpath -w "$PWD/tray/install.ps1")" -Uninstall
```

Dopo la chiusura puoi eliminare `%LOCALAPPDATA%\Launchpad`. Per verificare configurazione, creazione delle icone e menu senza avviare il servizio, esegui da PowerShell Windows:

```powershell
& "$env:LOCALAPPDATA\Launchpad\launchpad-tray.ps1" -Check
```

L'integrazione usa [NotifyIcon di Windows Forms](https://learn.microsoft.com/dotnet/desktop/winforms/controls/how-to-associate-a-shortcut-menu-with-a-windows-forms-notifyicon-component) e i [comandi WSL con distribuzione e utente espliciti](https://learn.microsoft.com/windows/wsl/basic-commands).

## Script tmux

I tab **Servizi** e **Sessioni tmux** mostrano una sezione per volta. La sezione tmux trova automaticamente ogni `~/tmux/<progetto>.sh`. Associa `kill-<progetto>.sh` per l'arresto e `switch-<progetto>.sh` per le opzioni aggiuntive, senza un registro centrale. Lo script di avvio deve accettare `--detach` per poter essere lanciato dalla pagina. Lo stato viene letto dalle sessioni tmux effettive; i servizi nella pagina mostrano il collegamento al relativo launcher.

Se la sessione ha un nome diverso dal file, aggiungi nelle prime 40 righe dello script `# launchpad-session: nome-sessione`. Per esporre opzioni nello script switch, aggiungi `# launchpad-actions: status opzione1 opzione2` e implementa quei nomi come primo argomento dello script. Le operazioni vengono eseguite solo dopo un clic nella pagina, con conferma per arresto e opzioni aggiuntive; lo stato e l'output compaiono nella card.
