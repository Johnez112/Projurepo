# Distributed Chat System

Hajautettu reaaliaikainen chat-alusta, rakennettu Distributed Systems -kurssin lopputyönä. Järjestelmä koostuu neljästä itsenäisestä mikropalvelusta jotka kommunikoivat keskenään XML-RPC:llä ja TCP-soketeilla. Käyttäjät liittyvät chattiin selaimen kautta ilman asennuksia.

---

## Arkkitehtuuri

```
Selain  ──HTTP/REST──►  [Web Gateway :5000]  ──XML-RPC──►  [Auth Service :8001]
                                              ──XML-RPC──►  [History Service :8002]
                        [Web Gateway :5000]  ──TCP──────►  [Chat Service :12345]
                        [Chat Service :12345] ──XML-RPC──►  [Auth Service :8001]
                                              ──XML-RPC──►  [History Service :8002]
```

Selain ei koskaan puhu suoraan Chat Servicelle — Web Gateway avaa TCP-yhteyden palvelimen puolesta ja välittää viestit selaimeen Server-Sent Events (SSE) -streamina. Tämä tarkoittaa että reaaliaikaiset viestit virtaavat selaimeen ilman sivunlatausta tai WebSocket-kirjastoja.

---

## Tiedostorakenne

```
Projekti/
│
├── auth_service.py      — Mikropalvelu: rekisteröinti, kirjautuminen, token-validointi
├── history_service.py   — Mikropalvelu: viestien tallennus ja haku tietokannasta
├── chat_service.py      — Mikropalvelu: reaaliaikainen TCP-chat, kanavat, käyttäjät
├── web_gateway.py       — REST API + SSE-silta selaimen ja chat-palvelun välillä
│
├── config.py            — Jaettu konfiguraatio (portit, osoitteet, DB-polut)
├── templates/
│   └── index.html       — Frontendin HTML-rakenne ja käyttöliittymän näkymä
├── static/
│   └── style.css        — Frontendin tyylit ja visuaalinen ulkoasu
│   └── app.js           — Frontendin toiminnallisuus ja kommunikointi backendin kanssa
│
├── auth.db              — SQLite: käyttäjätilit ja sessiotokenit (luodaan automaattisesti)
├── history.db           — SQLite: viestit aikaleimoineen (luodaan automaattisesti)
│
└── chat_client.py       — Vaihtoehtoinen terminaalikäyttöliittymä (ei tarvita web-UI:n kanssa)
```

---

## Teknologiat ja miksi ne valittiin

| Komponentti | Teknologia | Perustelu |
|---|---|---|
| Auth Service | Python XML-RPC + SQLite | XML-RPC on standardikirjasto, ei asennuksia. PBKDF2-salasanahash tietoturvaa varten. |
| History Service | Python XML-RPC + SQLite | Sama RPC-mekanismi osoittaa mikropalveluarkkitehtuurin — palvelut ovat itsenäisiä. |
| Chat Service | TCP Sockets + Threading | Jokainen käyttäjä saa oman säikeen. Nopea, suora yhteys ilman HTTP-ylikuormaa. |
| Web Gateway | Flask (REST + SSE) | Yksi julkinen sisäänkäynti. SSE mahdollistaa reaaliaikaisuuden ilman lisäkirjastoja. |
| Käyttöliittymä | HTML/CSS/JavaScript | Käyttäjä avaa selaimen — ei asennuksia, toimii kaikilla laitteilla. |

---

## Asennus ja käynnistys

### Vaatimukset
- Python 3.10 tai uudempi
- pip

### Asennus (kerran)

```bash
pip install flask
```

### Käynnistys

```bash
# Terminaali — Start Service 
python start_services.py
```
Odota kunnes näet: `Running on http://127.0.0.1:5000`

### Avaa selaimessa

```
http://127.0.0.1:5000
```

Järjestelmä on valmis. Useampi käyttäjä voi liittyä avaamalla saman osoitteen eri selainvälilehdillä tai eri koneilla (käytä silloin koneen IP-osoitetta, esim. `http://10.0.0.5:5000`).

---

## Käyttöliittymä — toiminnot selitettynä

### Rekisteröityminen ja kirjautuminen

Avaa `http://127.0.0.1:5000`. Näet kirjautumisnäkymän jossa on kaksi välilehteä: **Kirjaudu** ja **Rekisteröidy**.

- **Rekisteröidy** — luo uusi käyttäjätili. Käyttäjänimi 2–32 merkkiä, salasana vähintään 4 merkkiä. Tieto tallentuu `auth.db`-tietokantaan.
- **Kirjaudu** — kirjaudu olemassa olevalla tilillä. Onnistuneen kirjautumisen jälkeen palvelin palauttaa **session tokenin** (yksilöllinen satunnainen merkkijono) joka toimii todennuksena kaikissa myöhemmissä pyynnöissä.

### Pääikkuna kirjautumisen jälkeen

```
┌─────────────────────────────────────────────────────┐
│ DistChat  |  #general              Jomppe  [Kirjaudu ulos] │  ← Yläpalkki
├──────────┬──────────────────────────────────────────┤
│ KANAVAT  │                                          │
│ #general │   [viestit näkyvät tässä]                │  ← Viestialue
│ #testi   │                                          │
│          │                                          │
│ [uusi-k] │──────────────────────────────────────────│
│ PAIKALLA │  Kirjoita viesti...          [Lähetä]    │  ← Syöttökenttä
│ Jomppe   │  /help · /pm · /users · /channels       │
│ Alice    └──────────────────────────────────────────┘
└──────────┘
```

**Yläpalkki** näyttää aktiivisen kanavan nimen (`#general`), kirjautuneen käyttäjän nimen ja kirjautumisnappulan.

**Vasen sivupalkki** on jaettu kahteen osaan:
- **Kanavat** — lista kaikista aktiivisista kanavista. Päivittyy automaattisesti 5 sekunnin välein. Klikkaamalla kanavan nimeä vaihtaa kanavaa.
- **Paikalla** — lista käyttäjistä jotka ovat tällä hetkellä samalla kanavalla. Päivittyy heti kun joku liittyy tai poistuu.

### Viestien lähettäminen

Kirjoita viesti tekstikenttään ja paina **Enter** tai klikkaa **Lähetä**. Shift+Enter tekee rivinvaihdon lähettämättä.

Viesti kulkee seuraavaa reittiä:
1. Selain lähettää POST `/api/chat/send` → Web Gateway
2. Gateway välittää viestin TCP-soketin kautta Chat Servicelle
3. Chat Service lähettää viestin kaikille kanavan käyttäjille
4. Muiden käyttäjien selaimet vastaanottavat viestin SSE-streamin kautta
5. Viesti tallennetaan History Serviceen tulevia kirjautumisia varten

### Kanavan vaihtaminen

**Klikkaamalla sivupalkista:** klikkaa kanavan nimeä vasemmassa palkissa.

**Kirjoittamalla:** kirjoita `/join kanavannimi` viestikentässä.

Molemmissa tapauksissa:
- Yläpalkin kanavanimi vaihtuu
- Viestialue tyhjenee
- Kanavan viimeiset 10 viestiä ladataan historiasta automaattisesti
- Paikalla-lista päivittyy uuden kanavan käyttäjiin

### Uuden kanavan luominen

Kirjoita kanavan nimi sivupalkin alareunassa olevaan kenttään ja paina Enter tai klikkaa **Liity**. Kanava luodaan automaattisesti jos sitä ei ole olemassa. Kanavannimi muunnetaan automaattisesti pieniksi kirjaimiksi ja välilyönnit korvataan väliviivoilla.

### Historia

Kun liityt kanavalle (kirjautumisen yhteydessä tai `/join`-komennolla), viimeiset viestit ladataan automaattisesti. Voit hakea lisää historiaa komennolla `/history 50` (numero = montako viestiä).

Historia näkyy viestialueessa vaakaviivan takana erillisessä lohkossa jotta tiedät mitkä ovat vanhoja ja mitkä uusia viestejä.

### Kirjautuminen ulos

Klikkaa **Kirjaudu ulos** yläpalkissa. Tämä:
- Sulkee TCP-yhteyden Chat Serviceen
- Mitätöi session tokenin Auth Servicessä
- Ilmoittaa muille käyttäjille poistumisesta

---

## Chat-komennot

Komennot kirjoitetaan viestikentässä kuten tavalliset viestit.

| Komento | Esimerkki | Mitä tekee |
|---|---|---|
| `/help` | `/help` | Näyttää kaikki komennot chatissa |
| `/join <kanava>` | `/join yleinen` | Vaihtaa kanavaa tai luo uuden |
| `/channels` | `/channels` | Listaa kaikki aktiiviset kanavat ja käyttäjämäärät |
| `/users` | `/users` | Listaa käyttäjät nykyisellä kanavalla |
| `/history [n]` | `/history 30` | Näyttää viimeiset n viestiä (oletus 20) |
| `/pm <käyttäjä> <viesti>` | `/pm Alice hei!` | Lähettää yksityisviestin toiselle käyttäjälle |
| `/quit` | `/quit` | Katkaisee yhteyden |

### Viestivärien merkitys

| Väri | Mitä tarkoittaa |
|---|---|
| Värikoodattu avatar + nimi | Normaali chat-viesti, väri uniikki per käyttäjä |
| 🟢 Vihreä teksti | Järjestelmäilmoitus (kanavan vaihto, käyttäjälista) |
| 🔵 Vihreä reunaviiva | Liittymis- ja poistumis-ilmoitukset (`*** Jomppe joined #general ***`) |
| 🟡 Kultainen reunaviiva | Yksityisviesti sinulle tai sinulta |
| 🔴 Punainen reunaviiva | Virheviesti |
| Harmaa kursiivi | Muu järjestelmäviesti |
| Monospace-kehys | `/help`-komennon tuloste |

---

## REST API — tekninen kuvaus

Web Gateway tarjoaa seuraavat HTTP-endpointit. Näitä voi testata myös suoraan selaimesta tai curl-komennolla.

```bash
# Järjestelmän tila — kaikki palvelut
curl http://localhost:5000/api/health

# Rekisteröinti
curl -X POST http://localhost:5000/api/register \
     -H "Content-Type: application/json" \
     -d '{"username":"alice","password":"salis123"}'

# Kirjautuminen (palauttaa tokenin)
curl -X POST http://localhost:5000/api/login \
     -H "Content-Type: application/json" \
     -d '{"username":"alice","password":"salis123"}'

# Viestit kanavalta
curl http://localhost:5000/api/history/general?limit=20

# Kaikki kanavat viestimäärineen
curl http://localhost:5000/api/channels

# Järjestelmätilastot (viestimäärät, käyttäjät)
curl http://localhost:5000/api/stats
```

---

## Miten hajautuneisuus näkyy käytännössä

Jokainen palvelu on täysin itsenäinen prosessi omassa terminaalissaan:

- **Auth Service** voidaan käynnistää uudelleen ilman että chat katkeaa — jo kirjautuneet käyttäjät jatkavat normaalisti (heidän tokeniensa validointi epäonnistuu väliaikaisesti mutta yhteys pysyy)
- **History Service** voidaan pysäyttää — viestit välittyvät edelleen reaaliajassa, vain tallennus ja historia eivät toimi
- **Chat Service** hallitsee säikeet: jokaiselle käyttäjäyhteydelle luodaan oma `threading.Thread`, ja viestien tallennus History Servicelle tapahtuu erillisessä daemon-säikeessä jotta se ei hidasta viestien välitystä
- **Web Gateway** toimii ainoana julkisena sisäänkäyntinä — kaikki selainpyynnöt kulkevat sen kautta ennen kuin ne päätyvät oikealle palvelulle

Palvelut kommunikoivat kahdella eri protokollalla: **XML-RPC** (Auth ja History, pyyntö-vastaus-malli) ja **TCP-socketit** (Chat Service, pysyvä yhteys). Tämä on tietoinen arkkitehtuurivalinta joka osoittaa erilaisten hajautettujen kommunikaatiomallien käyttöä.

---

## Tietokannat

| Tiedosto | Palvelu | Sisältö |
|---|---|---|
| `auth.db` | Auth Service | Taulu `users` (käyttäjänimi, PBKDF2-salasanatiiviste, suola, luontiaika) + taulu `tokens` (token, käyttäjänimi, vanhentumisaika) |
| `history.db` | History Service | Taulu `messages` (kanava, käyttäjänimi, viesti, aikaleima) + indeksit nopeaa hakua varten |

Tietokannat luodaan automaattisesti ensimmäisellä käynnistyskerralla. Tiedostoja ei tule lisätä versionhallintaan (ne ovat `.gitignore`:ssa).

---

## Vikasietoisuus

- Jos Chat Service menettää yhteyden Auth Serviceen RPC-kutsun aikana, virhe kirjataan lokiin mutta ohjelma jatkaa toimintaansa
- Jos History Serviceen tallennus epäonnistuu, viesti välitetään silti reaaliajassa — tallennus tapahtuu erillisessä säikeessä eikä blokaa pääsäiettä
- Kun käyttäjä sulkee selaimen tai yhteys katkeaa, Chat Service tunnistaa katkenneen sokettiyhteyden, poistaa käyttäjän rekistereistä ja ilmoittaa muille automaattisesti
- `/api/health` -endpoint tarkistaa kaikkien palveluiden tilan ja palauttaa `"overall": "degraded"` jos jokin palvelu ei vastaa
