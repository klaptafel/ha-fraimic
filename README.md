# Fraimic E-Ink Canvas — Home Assistant integratie

> **⚠️ Niet-officiële, community integratie.** Niet gemaakt of onderhouden
> door Fraimic. Gebouwd op basis van hun publieke REST API-documentatie en
> een reverse-engineered `.bin`-conversietool (zie bronnen hieronder).
> Fraimic kan de API op elk moment zonder aankondiging wijzigen — dit kan
> deze integratie stukmaken. Gebruik op eigen risico, geen garanties, geen
> officiële support van Fraimic.

Custom component voor het Fraimic e-ink art frame. Gebaseerd op:
- https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide (REST API)
- https://github.com/Fraimic/fraimic_bin_converter (Spectra 6 .bin formaat)

## Wat je krijgt

- **Sensors**: batterij % en spanning (via het lichtgewicht `/api/battery`,
  elke 60s gepolld, met `state_class: measurement` voor grafieken), WiFi
  RSSI/IP, volgende geplande refresh, en `last_seen` (via `/api/info`, elke
  5 minuten). Timestamp-velden gebruiken `device_class: timestamp` met een
  echt `datetime`-object, niet een string. Firmwareversie zit alleen nog in
  de update-entity, en `last_boot`/`last_refresh`/registratiestatus zijn
  bewust geschrapt — te veel overlap met `last_seen` voor te weinig extra
  informatie.
- **Binary sensors**: laadstatus (`battery_charging`), oplaadkabel
  aangesloten (`plug`) en `reachable` — booleans horen hier, niet als
  tekst-sensor.
- **Buttons**: Restart, Sleep, Refresh Display — met nette Nederlandse
  foutmeldingen voor de gedocumenteerde foutcodes (bijv. "kan niet in
  slaap: er zit een oplaadkabel in")
- **Media player "Display"**: open de media-browser in de HA app/dashboard,
  blader naar een foto (Local Media, camera snapshot, etc.) en tik erop —
  wordt automatisch geconverteerd naar het 1200x1600 Spectra 6 `.bin`
  formaat en naar het frame gestuurd.
- **"Now showing" beeld**: geen aparte `image` entity meer — de
  `Display` media player toont de laatst verstuurde afbeelding via zijn
  eigen `entity_picture` (dezelfde albumhoes-achtige mechaniek als een
  muziekspeler gebruikt via `async_get_media_image`). Dat is niet de
  originele bronfoto, maar exact de gequantiseerde/geditherde versie zoals
  die op het scherm staat. Zolang je alleen via deze integratie naar het
  frame stuurt komt dat overeen met wat er nu op het scherm staat — de
  Fraimic API heeft namelijk geen endpoint om de framebuffer zelf terug te
  lezen.
- **Service `fraimic.send_image`**: stuur een pad op schijf direct naar het
  frame vanuit een automation/script, met keuze voor `fit`
  (`fit`/`fill`) en `dither` (verschillende algoritmes).
- **Firmware & model**: staan gewoon op de Device-pagina zelf
  (`sw_version`/`model` in het device registry record, centraal bijgehouden
  in `__init__.py`), niet als losse entity. Er is geen lokale
  "nieuwste versie beschikbaar"-detectie of install-mogelijkheid (firmware
  updates verlopen via Fraimic's eigen cloud OTA), dus een aparte
  `update`-entity voegde weinig toe en is verwijderd.

## Installatie

1. Kopieer de map `custom_components/fraimic` naar `/config/custom_components/`
   op je Home Assistant instantie (via Samba, SSH, Studio Code Server, of de
   Netlify/Vercel-achtige file manager add-on).
2. Herstart Home Assistant.
3. Ga naar **Instellingen → Apparaten & diensten → Integratie toevoegen**,
   zoek naar "Fraimic" en vul het adres van je frame in
   (bijv. `http://fraimic.local`).
4. Klaar — sensors, buttons en de media player verschijnen automatisch.

Pillow en numpy worden automatisch geïnstalleerd door Home Assistant via
`manifest.json` (`requirements`).

## Eigen GitHub-repo opzetten (Dependabot)

Deze map is klaargezet als https://github.com/klaptafel/ha-fraimic, met
Dependabot-ondersteuning voor de Python-dependencies. Placeholders zijn
al ingevuld in `manifest.json`.

Nog te doen:
1. **Push deze map** (inclusief `.github/`, `hacs.json`,
   `requirements.txt` en `custom_components/`) naar die repo, als dat nog
   niet gebeurd is.
2. **Dependabot gaat automatisch aan** zodra `.github/dependabot.yml` in
   de default branch staat (geen verdere actie nodig, tenzij je repo
   Dependabot org-breed heeft uitgeschakeld — dan aanzetten via Settings
   → Code security).

**Belangrijk om te snappen**: `requirements.txt` bestaat **puur voor
Dependabot** — Home Assistant leest dat bestand niet. Als Dependabot een
PR opent (bijv. "bump epaper-dithering to 5.2.0"), moet je diezelfde
versie-wijziging **met de hand overzetten** naar
`custom_components/fraimic/manifest.json`'s `requirements`-lijst, want
dát is wat HA daadwerkelijk gebruikt om te installeren. De twee bestanden
horen elkaar te spiegelen; Dependabot houdt alleen `requirements.txt`
actueel, niet `manifest.json`.

Optioneel: voeg de repo toe als **HACS custom repository**
(`hacs.json` staat er al klaar voor) zodat updates aan de integratie zelf
ook via de HACS-UI gaan i.p.v. handmatig kopiëren.

## Gebruik van de media browser

Ga naar de "Display" media player entiteit → bladerknop → kies bijv.
"Local Media" → selecteer een foto → tik op afspelen. HA stuurt de
media-content-id door, de integratie haalt de bytes op, converteert ze
on-the-fly (Floyd–Steinberg dithering naar de 6 panelkleuren) en post het
resultaat naar `/api/image`.

> Let op: dit gebruikt Pillow's ingebouwde Floyd–Steinberg quantizer, niet
> de originele Atkinson-implementatie uit `fraimic_bin_converter`. Het volgt
> wel exact dezelfde fit/enhance/pack-pipeline en levert een spec-correct
> bestand van 960.000 bytes op — de kleurweergave kan net iets anders ogen
> dan de losse Python-tool.

## Voorbeeld automation

```yaml
automation:
  - alias: "Verjaardagsfoto naar Fraimic"
    trigger:
      - platform: time
        at: "08:00:00"
    action:
      - service: fraimic.send_image
        target:
          entity_id: media_player.fraimic_display
        data:
          path: /config/www/fraimic/verjaardag.jpg
          fit: crop
          dither: true
```

## Beeldformaat

Bevestigd door Wendel: dit frame gebruikt het **Spectra 6 kleurformaat**
(6 kleuren, EL133UF1 controller) — niet het 4-bit grayscale formaat dat in
de officiële PDF-guide (v0.2.16) beschreven staat. Die guide lijkt op dit
punt verouderd of voor een ander model te gelden. De conversie in
`image_converter.py` volgt de packing uit
[fraimic_bin_converter](https://github.com/Fraimic/fraimic_bin_converter).
Mocht een afbeelding er toch verminkt uitzien, is dit het eerste om te
controleren.

## Beeldopties: fit en dithering

- **Fit**: `fit` (hele beeld zichtbaar, zwarte randen bij — CSS
  `object-fit: contain`) of `fill` (vult het frame, randen worden
  afgesneden — CSS `object-fit: cover`).
- **Dithering**: alle 9 algoritmes die `epaper-dithering` ondersteunt —
  `none`, `floyd_steinberg` (standaard), `atkinson` (matcht de originele
  `fraimic_bin_converter`), `ordered`, `burkes`, `stucki`, `sierra`,
  `sierra_lite`, `jarvis_judice_ninke`. Zonder de bibliotheek (zeldzaam
  randgeval, zie hieronder) hebben we alleen zelf een fallback voor
  `none`/`floyd_steinberg`/`atkinson`; de overige 6 vallen in dat geval
  terug op `floyd_steinberg` i.p.v. te crashen.

  Draait automatisch op de
  [`epaper-dithering`](https://github.com/OpenDisplay/epaper-dithering)
  bibliotheek (Rust-core) — **je hoeft hier zelf niets voor te doen**, hij
  staat gewoon in `manifest.json`'s `requirements` en Home Assistant
  installeert 'm automatisch bij het laden van de integratie, net als
  Pillow en numpy. Resultaat: sneller (~30ms i.p.v. seconden) en hogere
  kwaliteit (OKLab-kleurmatching, serpentine scanning tegen
  "worm"-artefacten) dan wat mogelijk is in pure Python. Er zit alléén in
  de *code* een fallback naar een eigen, tragere implementatie, puur als
  vangnet voor het (onwaarschijnlijke) geval dat er geen prebuilt wheel
  bestaat voor jouw specifieke HA-platform — dan faalt alleen deze
  integratie met een duidelijke pip-foutmelding in de logs.

  Gebruikt altijd het **theoretische** `ColorScheme.BWGBRY` palet (pure
  RGB) — een A/B-test tegen de "measured" `SPECTRA_7_3_6COLOR*` paletten
  (gekalibreerd voor het 7.3" Spectra paneel, niet ons 13.3"/31.5" paneel)
  wees uit dat `floyd_steinberg` + `bwgbry` het beste resultaat gaf, dus
  er is geen paletkeuze meer. Om diezelfde reden bieden we ook geen
  kleurcorrectie (`tone_compression`) aan: die knop van de bibliotheek
  heeft expliciet **geen effect bij theoretische paletten zoals BWGBRY**
  — zou dus gewoon niets doen zolang we bij `bwgbry` blijven.

**Via de media browser** (tikken op een foto) is er geen ruimte voor een
extra keuzedialoog, dus die gebruikt een **standaardwaarde** die je instelt
via **Instellingen → Apparaten & diensten → Fraimic → Configureren**.

**Via de `fraimic.send_image` service** kun je `fit`/`dither` altijd per
aanroep meegeven, ongeacht die standaardinstelling — handig voor
automations die bijvoorbeeld altijd `fill`+`atkinson` willen voor
portretfoto's.

## Device orientation (fysieke montage)

Instelling in **Configureren**: `device_orientation` (`portrait` /
`landscape`) — hoe het frame fysiek aan de muur hangt. Anders dan
fit/dither is dit **niet** overrulebaar per `send_image`-aanroep, want het
is een vast gegeven over het frame zelf, niet iets dat per foto verschilt.

Belangrijk om te weten:
- Het paneel's eigen buffer is en blijft **altijd** 1200×1600 (hardware-
  feit, verandert nooit). Bij `landscape` compose we intern tegen een
  1600×1200 "zichtbaar" canvas en roteren we het eindresultaat 90° terug
  naar die vaste buffervorm.
- **Nog steeds onbevestigd**: of dit conflicteert met een eventuele
  automatische rotatie door de accelerometer die Fraimic's marketing
  noemt. Test dit empirisch: als een landscape-gemonteerd frame al goed
  rechtop toont met `device_orientation: portrait` (dus zonder onze eigen
  rotatie), laat het dan gewoon op portrait staan.
- De rotatierichting bij `landscape` is een educated guess (rechtsom).
  Komt het resultaat ondersteboven of zijwaarts uit, dan is dat één regel
  om om te draaien in `image_converter.py` (`_fit_image`, de
  `result.rotate(-90, ...)` regel) — laat het weten dan pas ik 'm aan.

**Geen `auto_rotate` meer.** Die instelling (foto's automatisch draaien
zodat hun oriëntatie bij het canvas past) is verwijderd. Reden: hij bleef
ondanks meerdere pogingen onbetrouwbaar werken, en het probleem dat hij
oploste (staande foto op liggend frame, of andersom) los je net zo makkelijk
zelf op door een andere foto te kiezen of 'm vooraf te roteren. Minder
code, minder faalkansen, voorspelbaar resultaat.

## Bugfix: enhance/filter-volgorde bij fit

De helderheid/contrast/verzadiging-aanpassingen en scherpte-filters
gebeurden voorheen ná het fitten (dus ná het toevoegen van zwarte balken
bij `fit`-modus). Pillow's contrast-aanpassing rekent met het gemiddelde
van de hele afbeelding — bij een sterk letterboxed beeld (bijv. een
vierkante foto op een portrait-paneel, met 25%+ zwarte balken) trok dat
gemiddelde flink omlaag, waardoor de eigenlijke foto-inhoud verkeerd
verwerkt werd. Nu gebeurt enhance/filter vóór het fitten, op de foto
alleen — dat voorkomt zowel dit probleem als scherpte-artefacten op de
rand tussen foto en zwarte balk.

## Dry run

De `fraimic.send_image` service heeft een `dry_run` optie: converteert de
afbeelding en werkt de `Display` entity's foto (`entity_picture`) gewoon
bij, maar slaat de upload naar het frame zelf over. Handig om
fit/dither/device_orientation te testen zonder telkens de 20-30 sec
refresh-cyclus van het frame te moeten afwachten — check gewoon de
entity_picture in je dashboard. Alleen beschikbaar via de service, niet
via de media browser (die heeft geen manier om extra parameters mee te
geven).

## Geen wachtrij bij herhaald tikken

Als je op een afbeelding in de media browser tikt terwijl de vorige nog
aan het converteren/versturen is (dat duurt met Atkinson-dithering een
paar seconden extra), krijg je nu een duidelijke foutmelding in plaats van
dat er stilletjes een wachtrij ontstaat. Gewoon even wachten tot de vorige
klaar is en opnieuw tikken.

## Media browser: lokale bestanden

Voor `media-source://` items uit lokale mappen (Local Media, configured
`media_dirs`) leest de integratie het bestand direct van schijf, in plaats
van een HTTP-request te doen op de resolved URL. Reden: die URLs vereisen
normaal een auth-token voor requests buiten de HA-frontend om, wat 401/403
foutmeldingen gaf. Voor andere media-bronnen (bijv. camera's) valt de
integratie terug op de oude resolve+fetch aanpak, best-effort.

## Beschikbaarheid (sleepy device)

Het frame is een battery-powered, "sleepy" device: het is alleen bereikbaar
terwijl het wakker is (kort, na een tik of z'n eigen refresh-schema) en
volledig onbereikbaar tijdens deep sleep. Alles instant op `unavailable`
zetten bij elke gemiste poll zou vooral ruis opleveren, dus:

- **`binary_sensor.reachable`**: reflecteert direct of de laatste poll
  lukte. Deze mag en zal regelmatig aan/uit wisselen — dat is normaal en
  juist de bedoeling (aan = net wakker geweest, uit = slaapt/onbereikbaar).
  Blijft zelf altijd `available`.
- **`sensor.last_seen`**: tijdstip van het laatste succesvolle contact.
  Ook altijd `available`, zodat je juist kan zien hoe lang het al stil is
  wanneer dat ertoe doet.
- **Alle overige entities** (sensors, buttons, media player, image,
  update) blijven hun laatst bekende waarde tonen zolang het frame binnen
  `UNAVAILABLE_AFTER` (standaard 72 uur, zie `const.py`) nog gehoord is.
  Pas na langdurige stilte — een sterker signaal voor een echt probleem
  dan "net getikt tijdens een slaapmoment" — slaan ze om naar
  `unavailable`. Sluit dit tijdsvenster niet aan bij jouw gebruikspatroon
  (bijv. het frame wisselt elke paar dagen pas van afbeelding), pas
  `UNAVAILABLE_AFTER` in `const.py` aan.

## Adres wijzigen (reconfigure)

IP veranderd, of frame vervangen? Ga naar **Instellingen → Apparaten &
diensten → Fraimic → drie puntjes → Herconfigureren** en vul het nieuwe
adres in. Geen verwijderen/opnieuw toevoegen meer nodig. Er zit een check
in: als het ingevulde adres bij een ándere fysieke frame hoort dan
waarvoor je aan het herconfigureren bent, wordt dat geweigerd.

## Stabiele unique_id's

Entities en het apparaat gebruiken de hardware `device_key` uit
`/api/info` als identifier (met de host als terugvaloptie voor frames die
geen `device_key` teruggeven), in plaats van HA's interne `entry_id`.
Voordeel: verwijder je de integratie en voeg je hetzelfde frame opnieuw
toe, dan herkent HA het weer als hetzelfde apparaat — geen kwijtgeraakte
geschiedenis, aangepaste namen, area-toewijzing of automations die naar
een oude entity_id verwijzen.

## Foutafhandeling

Foutcodes uit de officiële guide worden vertaald naar duidelijke Home
Assistant meldingen (zie `api.py`):

| Actie | Fout | Betekenis |
|---|---|---|
| Upload | `invalid_image_size` | bestand is niet exact 960.000 bytes |
| Upload | `file_too_large` | bestand > 1 MB |
| Upload | `unsupported_content_type` | geen `.bin` verstuurd |
| Upload | `buffer_not_ready` | frame is bezig, probeer later opnieuw |
| Sleep | `charging_cable_connected` | slaapstand geblokkeerd tijdens opladen |

## Extra velden uit een echte /api/info response

Een live response (firmware v0.2.26) bevestigde een aantal velden die niet
in de officiële guide staan, nu verwerkt als *attributen op bestaande
sensoren* in plaats van losse entities (minder clutter):

- **`WiFi Signal`** sensor: attributen `ssid`, `band`, `channel`, `bssid`,
  `mac_address`
- **`Next Scheduled Refresh`** sensor: attributen `interval_days`, `hour`
  (het frame's eigen refresh-schema, bijv. "elke 4 dagen om 03:00")
- **`update.Firmware`** entity: attribuut `build` (firmware build-hash)

Eén nieuwe entity, omdat het een concreet aan/uit gezondheidssignaal is:

- **`binary_sensor.render_problem`** (`device_class: problem`) — aan
  zodra `display.render_failures > 0`, met `render_attempts` en
  `render_failures` als attributen. Geschikt om een notificatie op te
  hangen.

**Nog niet beschikbaar:** Temperature, Current, Cycles en Health (SOH)
staan wel op de `/info` portal-pagina maar niet in `/api/info`'s JSON —
die komen kennelijk uit een andere bron. Als je bevestigt waar (bijv. een
`/battery-stats` pagina analoog aan `/wifi-stats`), kan ik die er ook bij
pakken.

## Taal (NL/EN)

Config flow (setup/reconfigure), foutmeldingen en de `fraimic.send_image`
service zijn nu beschikbaar in het Engels (`strings.json`, de bron) en het
Nederlands (`translations/nl.json`) — Home Assistant kiest automatisch op
basis van de taalinstelling van de gebruiker. `translations/en.json` is een
letterlijke kopie van `strings.json` voor de duidelijkheid.

Runtime-foutmeldingen (bijv. "kan niet in slaap: oplaadkabel aangesloten")
gebruiken HA's `translation_key`-mechanisme (`HomeAssistantError(
translation_domain=..., translation_key=...)`) in plaats van hardcoded
tekst, en volgen dus ook de taalinstelling.

**Bewuste beperking:** entity-namen zelf (bijv. "Battery", "Restart",
"Now Showing") staan hardcoded in het Engels in de Python-code, niet via
het vertaalsysteem. Dat is gangbare praktijk voor de meeste HA-integraties
en het scheelt een forse refactor (elke `SensorEntityDescription` zou een
`translation_key` nodig hebben plus een `entity`-sectie per taal). Zeg het
als je dit ook vertaald wilt hebben.

## Bekende beperkingen / TODO

- Alleen lokale netwerktoegang, geen authenticatie — zoals de originele
  API ook geen auth vereist.
- `epaper_dithering` biedt ook exposure/saturation/shadows/highlights/
  tone/gamut-compressie knoppen — bewust nog niet gebouwd, mogelijk later
  als los verzoek.
