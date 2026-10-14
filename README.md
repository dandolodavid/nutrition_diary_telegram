# 🍽️ Nutrition Diary

Un bot Telegram che tiene il diario alimentare al posto tuo: gli scrivi (o gli detti, o gli fotografi) cosa hai mangiato e lui registra **kcal, proteine, carboidrati e grassi**, li confronta con i tuoi target e ti mostra l'andamento di giorni, settimane e peso.

Il punto di forza: **impara le cose che mangi di solito**. Gli alimenti che ha già visto li calcola da solo, in modo deterministico e senza consumare quota AI; l'LLM interviene solo per quello che non conosce.

---

## ✨ Cosa sa fare

- **Registrare un pasto** da testo, vocale o foto del piatto. Ogni alimento diventa una voce separata, con la sua quantità.
- **Calcolare senza AI** gli alimenti già noti («250 g yogurt greco»), anche con refusi e con parole in ordine diverso.
- **Imparare da solo**: ogni stima dell'LLM con grammi dichiarati da te finisce nel tuo database personale al grammo.
- **Target giornalieri** impostati a mano o estratti dal PDF della dieta del nutrizionista.
- **Statistiche** di giorno/settimana/mese/periodo personalizzato rispetto ai target.
- **Riepilogo** alimento per alimento di oggi o di un giorno a scelta (calendario).
- **Peso**: registrazione quotidiana e grafico del trend con media settimanale.
- **Database alimenti** consultabile (`/db`) e modificabile (`/edit_db`).
- **Resilienza all'AI**: catena di modelli Gemini con fallback automatico, ritentativi differiti sui rate limit e messaggi chiari quando la quota giornaliera è finita.

## 💬 Comandi

| Comando | Cosa fa |
|---|---|
| `/start` | Recap di cosa fa il bot |
| `/diary` | Torna alla modalità diario (e annulla qualunque conversazione aperta) |
| `/set` | Imposta i target giornalieri: a mano (`2200 150 220 70`) o caricando il PDF della dieta |
| `/stats` | Andamento di oggi, settimana, mese o periodo scelto rispetto ai target |
| `/summary` | Cosa hai mangiato oggi o in un altro giorno, alimento per alimento |
| `/remove` | Toglie una voce registrata (oggi o un altro giorno) |
| `/weight` | Registra il peso di oggi (`/weight 78,4` oppure il bot te lo chiede) |
| `/check_weight` | Trend del peso: media settimanale delle ultime 8 settimane, con grafico |
| `/db` | Mostra il tuo database alimenti (valori per 100 g) e poi chiede cosa cercare. `/db yogurt` cerca direttamente |
| `/edit_db` | Cambia nome o valori di un alimento del database, oppure eliminalo |

Fuori da un comando, qualsiasi testo, vocale o foto è un **pasto da registrare**.

## 🧠 Come funziona la cache al grammo

```
messaggio ──► spezzato in segmenti "alimento + grammi"
                 │
                 ├─ alimento noto ──► valori_per_grammo × grammi        (deterministico, senza AI)
                 └─ alimento ignoto ──► LLM (solo per quella parte)
                                           │
                       risultato salvato nel diario ◄──┘
                       e imparato nel database per la prossima volta
```

**Esempio.** Scrivi:

```
250 g yogurt greco
200 g pizza margherita
```

Lo yogurt è già nel tuo database e viene calcolato subito; all'LLM arriva solo `200 g pizza margherita`. Il riepilogo riporta una riga «📚 N alimenti calcolati dal tuo database (senza AI)». La volta dopo anche la pizza è nel database. Se tutto il messaggio è noto, l'LLM non viene nemmeno chiamato.

**Riconoscimento dei nomi**
- Insensibile a maiuscole, accenti, apostrofi (`Kellogg's` = `kelloggs`), numeri/percentuali (`0%`) e parole di riempimento («ho mangiato», «di», «del»…).
- L'ordine delle parole non conta; singolare/plurale e maschile/femminile sono equivalenti (`mela`/`mele`).
- Tollera i refusi (`yougrt greco` → *Yogurt greco*) ma solo sulle parole di almeno 5 lettere: `pollo` e `polpo` non si confondono mai.
- Serve lo stesso numero di parole: «yogurt greco» non matcha «yogurt greco fragola». Se il match è ambiguo o assente, decide l'LLM.
- Accetta `250 g`, `250g`, `250 grammi`, `1,5 kg`, `87,5 g`; gli alimenti si separano con a capo, virgola, `;`, `+` o «e».

**Come si riempie il database**
1. **Al primo avvio**, dallo storico dei pasti scritti o dettati (le foto sono escluse: lì i grammi sono una stima dell'LLM, non un dato tuo). Per ogni alimento vale l'ultimo valore registrato.
2. **Strada facendo**, da ciò che l'LLM stima, ma solo se i grammi li hai scritti tu. Il modo in cui hai scritto l'alimento diventa un *alias* («pasta al sugo» → *Spaghetti al pomodoro*).
3. **A mano**, con `/edit_db`.

I valori già salvati non vengono mai sovrascritti da nuove stime: i calcoli restano stabili nel tempo.

**Se l'LLM chiede un chiarimento** su una parte del messaggio, gli alimenti già calcolati restano in sospeso per 15 minuti e vengono salvati insieme alla tua risposta.

## 🏗️ Architettura

- **Python 3.12** con [python-telegram-bot](https://python-telegram-bot.org) (polling) e job queue per i ritentativi.
- **[Agno](https://docs.agno.com)** + **Google Gemini** per stima nutrizionale, trascrizione dei vocali e lettura del PDF dieta. Output strutturato (Pydantic) e memoria delle conversazioni, così non ti richiede di nuovo cose che ha già capito.
- **CREA / alimentinutrizione.it**: strumento di ricerca per gli alimenti generici (pollo, pane, mele…), usato dall'LLM per dati più affidabili della sola conoscenza del modello.
- **PostgreSQL 16** per diario, target, peso e database alimenti.
- **Docker Compose**: due servizi, `db` e `bot`.

```
nutrition_diary/
├── docker-compose.yml
├── .env.example
└── bot/
    ├── main.py              # registra comandi e handler, bootstrap del database alimenti
    ├── config.py            # variabili d'ambiente e catena dei modelli
    ├── food_cache.py        # cache pre-LLM: parsing, matching, apprendimento
    ├── retry.py             # ritentativi differiti su rate limit
    ├── charts.py            # grafico del peso (matplotlib)
    ├── calendar_kb.py       # tastiera calendario inline
    ├── pdf_ingest.py        # testo dai PDF della dieta
    ├── agents/              # agenti Gemini: nutrizione, dieta, trascrizione
    ├── handlers/            # un modulo per comando (diary, foods, edit_foods, stats…)
    ├── tools/               # ricerca nel database CREA
    ├── models/              # schemi Pydantic
    ├── db/                  # schema.sql e repository (SQL)
    └── assets/              # indice locale degli alimenti CREA
```

### Modello dati

| Tabella | Contenuto |
|---|---|
| `users` | Utenti Telegram |
| `targets` | Target giornalieri (kcal, P, C, G) e origine (`manual` / `diet_pdf`) |
| `diary_entries` | Un record per messaggio, con i totali |
| `diary_items` | Una riga per alimento dentro ogni entry |
| `weight_entries` | Un peso al giorno (una nuova misura dello stesso giorno sostituisce la precedente) |
| `foods` | Database al grammo per utente: nome, chiave normalizzata, alias, valori per grammo, origine (`history` / `llm` / `manual`) |

Lo schema è idempotente (`CREATE TABLE IF NOT EXISTS`) e il bot lo applica a ogni avvio, quindi aggiornare un database esistente non richiede migrazioni manuali.

## 🚀 Avvio

**Prerequisiti:** Docker con Compose, un bot Telegram creato con [@BotFather](https://t.me/BotFather) e una chiave API Gemini da [Google AI Studio](https://aistudio.google.com).

```bash
cp .env.example .env      # poi compila i valori
docker compose up -d --build
```

Verifica che sia partito:

```bash
docker compose logs -f bot
```

Per aggiornare dopo una modifica al codice:

```bash
docker compose up -d --build bot
```

Dopo aver cambiato `.env` (per esempio la chiave Gemini) il container va ricreato, perché le variabili si leggono solo alla creazione:

```bash
docker compose up -d --force-recreate bot
```

### Configurazione (`.env`)

| Variabile | Descrizione |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot da BotFather |
| `GEMINI_API_KEY` | Chiave Gemini dedicata a questo progetto |
| `GEMINI_MODEL_PRIORITY` | Modelli in ordine di preferenza, separati da virgola. Se manca, vale l'elenco predefinito in `bot/config.py` |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Credenziali del database |
| `DATABASE_URL` | Es. `postgresql+psycopg://nutrition:<password>@db:5432/nutrition` |

> ⚠️ `.env` contiene segreti ed è escluso da git. Non committarlo.

## 🛡️ Gestione di rate limit e quota AI

- **Catena di modelli**: ogni richiesta prova i modelli di `GEMINI_MODEL_PRIORITY` nell'ordine e passa al successivo su qualsiasi errore (rate limit, 503, modello ritirato). Ogni modello ha una quota gratuita separata, quindi più modelli distinti significano più resilienza.
- **Ritentativi**: se tutti falliscono per un intoppo momentaneo, il bot avvisa e riprova da solo dopo circa 65 secondi, fino a 3 tentativi totali: non serve rimandare il messaggio.
- **Quota giornaliera esaurita**: il bot lo dice subito, senza ritentare, perché non si risolve prima del reset di Google.
- **Con l'AI fuori uso** i messaggi composti solo da alimenti già nel database continuano a funzionare.

## 📝 Note e limiti

- Il database alimenti è **per utente**: i tuoi valori non influenzano quelli di altri.
- Le voci già registrate nel diario non cambiano se modifichi un alimento: il nuovo valore vale dai pasti successivi.
- Una parola sola senza grammi (es. «pane») non è una voce calcolabile e va all'LLM.
- Gli alimenti simili hanno voci distinte (es. «Special K» e «Special K Original»); non c'è ancora un comando per unirli, ma con `/edit_db` puoi eliminare quella che non serve.
- Le stime dell'LLM restano stime: per gli alimenti che mangi spesso conviene verificare i valori con `/db` e correggerli con `/edit_db` sull'etichetta del prodotto.
