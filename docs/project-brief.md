# Codex Dashboard — arbetsunderlag

Projektmapp: valfri lokal checkout av detta repository.

Bygg en lokal terminalvy som startas med `tyrell`. Verktyget och dess inställningar ska vara fristående från de projekt agenterna arbetar i och ska inte checkas in i dessa projekt.

## Önskad funktion

- Översikt med antal agenter och hur många som arbetar eller väntar.
- Navigerbar sidopanel med agenter/chattar och kort aktuell aktivitet.
- Växla mellan chattar och skriv till vald agent i ett chattfält längst ner.
- Visa körtid, senaste aktivitet och faktisk väntan på svar eller godkännande. Lång tystnad får anges som möjlig inaktivitet, inte som bevis att agenten fastnat.
- Visa planerade, pågående och klara uppgifter, med gul punkt för ej påbörjat och kryssruta för klart.
- Visa och välj modell och resonemangsnivå för kommande arbete.
- Starta uppgifter manuellt vid behov, i separata Git-worktrees. Användarens vanliga checkout och branch ska lämnas orörda.
- Spara chattar och dashboardens tillstånd. Bakgrundsarbete ska fortsätta när terminalvyn stängs.
- På macOS: förhindra automatisk systemvila medan uppgifter arbetar; skärmsläckning och låsning ska tillåtas. Avstängning och stängt laptoplock omfattas inte av detta löfte.

## Befintligt underlag

Installerad Codex CLI har tidigare identifierats som 0.155.0. `codex app-server proxy` erbjuder ett JSON RPC-gränssnitt till den gemensamma daemonen. Undvik att stoppa eller starta om den daemonen eftersom andra chattar kan använda den.

Katalogen `schema/` innehåller protokollscheman genererade av den installerade CLI-versionen. Relevanta metoder är initialize, thread/list, thread/read, thread/start, thread/resume, turn/start, turn/steer, turn/interrupt och model/list. Meddelanden ger aktuell status, strömmad text och planuppdateringar.

Tänkt implementation: terminalgränssnitt, separat beständig lokal tjänst, lokal IPC och worktrees utanför användarens ordinarie checkout. Slutlig teknisk utformning ska verifieras genom implementation och tester.

## Arbetsstatus

Dashboarden är implementerad och installerad (2026-09-18). Starta med `tyrell` i en ny terminal. Se `README.md` för kommandon, begränsningar och verifiering. Implementation finns i `tyrell/`, startpunkt `dashboard.py`.

Verifierat: 15 automatiska tester, läsning och återanslutning av den ursprungliga chatten, terminalvyn i en riktig PTY samt en verklig Codex-tur i en temporär worktree medan klienten var frånkopplad. macOS vilospärr observerades under live-testet. Shell-integrationen bevarar den ursprungliga Codex-binären och vanliga kommandon.

Uppdaterat 2026-09-19: ny färgsatt terminalvy med separata chatt- och verktygsflikar, tydliga avsändare, aktivitetsanimation, mus/styrplatteskroll och klick. `/remove`, `/hidden`, `/archive`, `/archives` och `/restore` hanterar sidopanel och arkiv. 26 tester passerar; musstödet har provats i en riktig PTY och arkivering/återställning mot Codex med en testchatt. Användaren bad att ta bort `Konfigurera Ghostty-terminal`; tråden `01a0b574-4a0b-7b83-b5b4-fe1b5b274fc1` är permanent dold från sidopanelen tills användaren väljer att återställa den. Chatthistoriken är bevarad.

Protokollundersökning och tidigare enkla worktree-experiment finns i den ignorerade katalogen `.codex-dashboard.local` och filen `codex-background.local` i det ursprungliga testprojektet. De är inte den färdiga dashboarden.

Utveckling sker i denna projektmapp. Användaren har godkänt implementation och installation samt gett full åtkomst för att slippa återkommande frågor. Inga globala Codex-säkerhetsinställningar har ändrats av dashboarden.

## Verifiering före leverans

Verifiera statusövergångar, beständighet, återanslutning, separata worktrees, modellval, interaktion med vald chatt och att stängning av dashboarden inte stoppar agentarbete. Planerade uppgifter ska inte starta automatiskt. Visa godkännanden och frågor tydligt utan att automatiskt bevilja okända förfrågningar. Bevara den vanliga codex-kommandots funktion när dashboard-kommandot installeras.
