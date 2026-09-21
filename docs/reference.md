# Tyrell Agent Management

*They work. You take the credit.*

En lokal terminalvy för agenter: chattar i sidopanelen, aktuell aktivitet,
modell, uppgifter och ett chattfält längst ner. Separata Codex- och Copilot-agenter
kan köra uppgifter i samma dashboard.
Starta dashboarden med `tyrell` (`agent-hub` fungerar också); den befintliga genvägen
`tyrell` fungerar fortfarande. Se [installationsguiden](install.md)
för ett delbart wheel-paket, CLI-beroenden och inloggning. Kräver Python 3.9+ och
minst en ansluten leverantör. macOS är verifierat; Linux är experimentellt.

**F10 Settings** samlar anslutningar, installationshjälp, GitHub-värd och diagnostik.
Första starten öppnar Settings. Agentens projektspecifika val finns kvar i Setup.

I **Files → Open folder…** kan du navigera till en mapp och välja **OK**.
Mappen blir rot för Git-ändringarna i Files, utan att flytta agentens arbete.
**Use agent workspace** återgår till agentens projekt eller worktree.
Mappvalet behålls per agent medan vyn är öppen.

## Homebrew

En Homebrew-formula och reproducerbart releasearkiv kan förberedas med
`scripts/build_homebrew.py`. Se [publicering via Homebrew](homebrew.md).
Det kräver inget Homebrew-konto; tap och release publiceras på GitHub.

Ett GitHub Actions-workflow testar pull requests till `main`, bygger paket och
verifierar Homebrew-installationen. Versionstaggar skapar ett releaseutkast efter
godkända tester. Kod och formula ändras via feature branches och pull requests.
Se [releaseflödet och branchskydd](homebrew.md).

## Anslutningar och Copilot

Headern visar **Codex** och **Copilot** var för sig. Grönt betyder att tjänsten
är ansluten; **Sign in** betyder att Copilot behöver inloggning. **Unavailable**
betyder att inloggningen finns men modellistan inte kan hämtas. Om kontakten med
dashboardens bakgrundstjänst bryts markeras båda som **Offline**.
Klicka på statusen eller skriv `/connections` för detaljer och rapporterade modeller.
Modellerna är en katalog; varje modell har inte provkörts.

Installera GitHubs officiella Copilot CLI separat, exempelvis med Node.js 22+:

```sh
npm install --global --prefix "$HOME/.local" @github/copilot
tyrell login copilot
```

För GitHub Enterprise Cloud med egen värd:

```sh
tyrell login copilot --host https://company.ghe.com
```

Den valda värden sparas, så senare inloggningar använder samma värd. Copilot hanterar
OAuth och nyckelringen; dashboarden läser eller sparar inga inloggningstoken.
`copilot` hämtas från PATH eller `~/.local/bin/copilot`; `AGENT_HUB_COPILOT` kan ange
en annan installerad binär. Ingen automatisk installation eller uppdatering görs
när dashboarden startar. CLI-installationen för denna implementation verifierades
med version 1.0.86 och JSON-RPC-protokoll 3.

En separat anslutning använder CLI:ns serverläge för `ping`, `auth.getStatus` och
`models.list`. Den skapar inga Copilot-sessioner, skickar inga promptar och kör inga
agentverktyg. Copilot-modeller blandas inte in i Codex-agenternas modellval.
F3 väljer namn och sedan leverantör/modell. En Copilot-agent har en egen bestående
CLI-session och använder samma Setup, mappimport, worktrees och porttilldelning.
Chattsvar strömmas in; planer visas i Plan och verktygsaktivitet i Tools.
Frågor och godkännanden öppnas via Waiting eller `/requests`. Godkännanden gäller
bara den aktuella förfrågan. `/interrupt` avbryter Copilot-sessionens aktuella arbete.
Namnbyte, arkivering och återställning påverkar dashboardens katalog, inte CLI-historiken.

Copilot körs i en separat CLI-process som ägs av bakgrundstjänsten. Att stänga
vyn stoppar inte arbetet; att stoppa bakgrundstjänsten avbryter pågående Copilot-arbete.
Nästa meddelande återupptar den sparade sessionen, utan automatisk omsändning.
Codex-avbrott rensar inte Copilots frågor. Copilot kan användas utan Codex-anslutning.

Copilot använder CLI:ns och organisationens behörighetspolicy, inte Codex sandbox.
**Setup → Access → Copilot permissions** har två lägen:
- **Ask** visar förfrågningar när CLI behöver godkännande.
- **Autonomous** tillåter verktyg, filåtkomst och nätverk utan rutinmässiga dialoger.

Ändringen gäller nästa nya tur. Organisationens regler kan fortfarande stoppa
åtgärder och frågor till användaren är fortsatt interaktiva. Codex fil- och
sandboxreglage påverkar inte Copilot. Reasoning/speed använder modellens standard.
Tools visar kommandon och strömmad verktygsutdata, separat från Chat.
Files samlar Git-diffar, även för ändringar via shellkommandon. I en isolerad
worktree jämförs filerna med startcommitten; annars med HEAD. Även ändringar från
andra verktyg eller en överlämning ingår. Binära filer visas utan textdiff;
kända känsliga konfigurationsfiler visas utan innehåll. Git-insamlingen är begränsad
till 250 filer och 60 000 tecken per diff, och en begränsning visas i vyn.

**F6**, **/handoff** eller **Setup → Hand over to another agent** kopierar arbete
till en ny agent: välj namn och leverantör/modell, granska sammanhanget och skicka
sedan ett meddelande för att starta. Källagenten måste vara redo. En ny isolerad
Git-worktree utgår från källans HEAD och får dess ocommittade ändringar och
icke-ignorerade nya filer. Originalet ändras inte. Ignorerade beroenden, kända
lokala autentiseringsfiler och otrackade specialfiler kopieras inte; utelämnade
filer listas. Gränser: 8 MB tracked patch, 20 MB/1 000 otrackade filer.
Överlämningen inkluderar den senaste planen och upp till 12 senaste chattmeddelanden,
inte hela sessionens interna historik. Filöverlämning kräver ett Git-repo.
Den nya agenten börjar med interaktiva standardbehörigheter; välj autonom åtkomst
för den agenten i Setup om det önskas. Överlämning gör inga commits, pushes eller merges.

**Setup → Verify GHE repository access** kan kontrollera
Git-läsåtkomsten för ditt valda repo; datum och repo visas i Connections. Det innebär inte att
en separat GitHub MCP-connector har verifierats. Verifieringen ändrar inget i repot.
Anslutningsstatus sparas inte som bestående sanning; den kontrolleras på nytt
vid start och uppdateras automatiskt efter inloggning. Copilot-anslutningen lever
i dashboardens bakgrundstjänst och stoppas när den tjänsten stoppas.

`tyrell connections` visar samma status som JSON utan chatthistorik.
Se [installation](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli)
och [inloggning](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli)
i GitHubs dokumentation. Protokollanropen följer
[GitHubs SDK](https://github.com/github/copilot-sdk/blob/main/python/copilot/client.py).

## Starta

Se [installationsguiden](install.md). Starta med `tyrell`.

## Använd vyn

| Kommando/tangent | Funktion |
| --- | --- |
| Tab | Växla fokus: sidopanel → historik → Prompt |
| Piltangenter | Välj agent i sidopanelen, scrolla i historiken eller redigera i Prompt |
| Mushjul / styrplatta | Skrolla konversationen eller verktygsloggen |
| Klick | Välj agent, flik eller chattfält |
| F2 **Tabs** | Växla **Chat → Plan → Tools → Files → Processes → Setup → Chat** |
| Esc | Återgå direkt till **Chat**; stäng paneler eller avbryt formulär |
| `/tasks` | Öppna **Plan**, en checklista som uppdateras löpande |
| Ctrl+O / `/log` | Visa verktygens kommandon och utdata under **Tools**; `/chat` återgår till chatten |
| F3 | Skapa agent: namn → modell |
| F4 / `/archives` | Öppna arkivet; F4 återgår till agenterna |
| F5 | Byt namn på vald agent: skriv nytt namn, Enter sparar, Esc avbryter |
| F1 | Visa hjälpen |
| `/mouse` | Slå av/på musfångsten |
| Enter i chattfältet | Skicka ett meddelande; styr pågående arbete om agenten redan arbetar |
| Shift+Enter / Alt+Enter | Lägg till en ny rad i meddelandet |
| Cmd+Enter (Ghostty) | Skicka styrning till agenten vid nästa säkra avstämning |
| Piltangenter i chattfältet | Flytta markören och redigera texten |
| PageUp / PageDown | Bläddra i konversationen |
| `/new` | Skapa agent: namn → modell, utan krav på repository |
| `/start` | Starta den valda planerade uppgiften i en ny worktree |
| `/rename [nytt namn]` | Byt namn på vald agent; utan namn öppnas ett namnfält |
| `/models` | Visa modeller och resonemangsnivåer från din Codex-installation |
| `/model NAMN NIVÅ` | Välj modell och nivå för den valda chattens nästa tur |
| `/default NAMN NIVÅ` | Välj standard för nya uppgifter |
| `/request` / `/requests` | Läs och besvara väntande godkännanden/frågor |
| `/interrupt` | Avbryt den valda agentens pågående tur |
| `/remove [namn]` | Dölj vald eller namngiven agent i sidopanelen |
| `/hidden` | Visa agenter som dolts från sidopanelen |
| `/archive [namn]` | Arkivera vald eller namngiven inaktiv chatt i Codex |
| `/restore [namn]` | Lägg tillbaka en dold eller arkiverad agent i sidopanelen |
| `/agents` | Återgå till de vanliga agenterna |
| `/help` | Visa hjälpen |
| Ctrl+Q, Ctrl+C, `/quit` | Stäng vyn; bakgrundsarbetet fortsätter |

Klistra in flerradig text i terminaler som stödjer bracketed paste. Texten skickas
först när du trycker Enter. Esc stänger paneler eller avbryter ett formulär.
Inmatningsfältet bryter långa rader automatiskt, bevarar inklistrade radbrytningar
och växer upp till sex synliga rader. Längre utkast skrollas med markören.
Du kan klicka i texten för att placera markören. Alt+Enter fungerar för en ny rad
även i terminaler som inte skickar en separat tangentkod för Shift+Enter.

Scrollbaren till höger visar din position i historiken. Klicka i spåret eller dra
handtaget för att bläddra; mushjul och PageUp/PageDown fungerar som tidigare.
Klicka var som helst i sidopanelen för att fokusera den, eller på en agents
namn eller statusprick för att välja agenten. Flikarna Chat, Plan, Tools, Files, Processes och Setup är klickbara
och fokuserar innehållet när du öppnar dem.
I Ghostty visas en handpekare över agenter och flikar samt en I-balk över
markerbar chatttext och skrivfältet. Agenter visas alfabetiskt, en per rad, med en färgad statusprick till vänster.
Vald agent har understruket namn. Långa namn kortas med …; hela namnet sparas.
Tabba till historiken eller klicka i den. Piltangenterna scrollar historiken;
dra över text för att markera och tryck Cmd+C för att kopiera markeringen
till urklipp, utan sidopanel eller meddelanderamar. Musen kopierar aldrig automatiskt. Det behövs inget
separat kopieringsläge. Den markerade texten hålls stabil medan nya svar kommer;
Tab, Esc eller en ny scrollning återgår till uppdaterad historik. Utkastet bevaras.
På macOS används systemets `pbcopy`; Linux behöver `wl-copy` eller `xclip`.
Kopieringen visar varken instruktioner eller något kvitto i vyn.
Dashboarden använder Kitty-tangentprotokollet för Cmd+C. I Ghostty behövs
`keybind = performable:super+c=copy_to_clipboard:mixed`: terminalens egen
markering kopieras som vanligt, annars skickas Cmd+C till programmet.
Ladda om Ghosttys konfiguration med Cmd+Shift+, efter en ändring.
I Apples Terminal visas **[Copy]** bredvid agentnamnet när chatttext är
markerad. Klicket kopierar dashboardens markering med pbcopy, utan automatisk
kopiering eller kvitto. Detta är en reservväg för kopiering; Cmd+C-kopplingen
och byte av muspekarens form är inte lösta för Terminal.app. Cmd+V hanteras av
terminalen; dashboarden tar emot bracketed paste i Prompt.
Meddelandenas rubriker skrollas tillsammans med innehållet; inga fasta
”continued”-rubriker läggs ovanpå historiken.

Dina meddelanden visas direkt när du skickar dem. Kortet visar **Sending…** tills
Codex bekräftar mottagandet, därefter **Delivered**. Detta är ett leveranskvitto;
Codex skickar inget säkert läskvitto. Vid osäker leverans visas **Delivery unconfirmed**
och texten återställs i utkastet. Meddelanden matchas med egna id:n så att samma
text kan skickas flera gånger utan att serverns återkoppling skapar dubbletter.

Dashboardens gränssnitt är på engelska. Chattinnehåll, uppgiftstexter och agentnamn
behåller sitt språk. Skrivfältet heter **Prompt**. På Mac kan du behöva Fn+F2 eller
Fn+F5 om macOS använder funktionstangenterna för systemfunktioner.

**Setup** innehåller modell/hastighet, svarslängd, uppdragsbeskrivning, projektkontext,
tester, Node-processer/portar, Terraform, kodkvalitet och Git-överlämning. Inställningar
kan sparas för agenten, ett projekt eller som gemensamma förval. De är generella;
projektmetadata importeras bara till en valfri projektprofil. Läs [Setup-guiden](agent-setup.md).

Chattvyn visar blå kort märkta **YOU** och gröna kort märkta **AGENT**. Kommandon,
rå utdata och resonemang ligger separat under **TOOLS**. Där färgmarkeras
kommandon, strängar, tal, kommentarer och fel. Avsändaren syns även när du
skrollar mitt i ett långt meddelande. Färgtemat har stöd för både 256 färger
och enklare terminaler; textetiketterna fungerar även utan färg.
Agentsvar får terminalformatering för fetstil, rubriker, listor, inlinekod och
kodblock. Markeringar som stjärnor och kodstaket visas inte runt formaterad text;
kodblock behåller sitt innehåll. Kopiering hämtar den visade texten utan chattramar.
Vid flikbyte ritas terminalen om helt så att rester från verktygsutdata inte ligger kvar.

Sidopanelen visar gula agentnamn på en rad vardera, i bokstavsordning.
Vald agent har understruket namn utan bakgrundsmarkering. Status visas enbart
som en prick till vänster: **blå = Working**, **grön = Ready**, **gul = Waiting**.
Statusändringar påverkar inte ordningen. Långa namn kortas med … i listan.
`/rename Nytt namn` sparar namnet i Codex så att det finns kvar efter omstart.

Den rörliga aktivitetsstapeln och punkterna visar en aktiv agent, utan att ange
en uppskattad procent. Meddelanden visas bara i chatten, under avgränsningslinjen.
Planer visas enbart i **Plan**-fliken. **F2** växlar till fliken med
hela checklistan: **[✓] Done**, **[▶] In progress**, **[ ] Pending**.
Långa listor kan skrollas, och markeringarna uppdateras när agentens plan ändras.
Vid frågor och godkännanden stannar
animationen. Mushjul och styrplatta bläddrar i historiken även över skrivfältet;
piltangenterna redigerar utkastet och PageUp/PageDown bläddrar i historiken.

För meddelanden som skickas från dashboarden får agenten en instruktion att dela en
plan för **varje uppgift**, även en enda åtgärd eller ett kort svar, och ange ungefärlig återstående tid när det går att
bedöma. Tidsintervallet kommer från agenten och visas tillsammans med bedömningens
ålder. Dashboarden räknar inte fram en prognos från antalet färdiga uppgifter.
Saknas bedömning visas **Time remaining: not estimated**; en utgången bedömning markeras
för uppdatering. Instruktionen skickas även när ett meddelande styr en pågående
tur. Nya önskemål ska läggas till i den befintliga planen som uppgifter eller
deluppgifter, med prioritet i Plan-fliken och ett vanligt svar i chatten som avslutas med **Plan uppdaterad**. Checklistan visas bara i Plan-fliken. Ofärdiga uppgifter
behålls tills användaren ändrar eller avbryter dem. I platta planer skrivs
deluppgifter som `Huvuduppgift: deluppgift`. Agenten behöver fortfarande publicera
planen; dashboarden skapar inte uppgifter från chattext på egen hand.
En planförklaring med `ETA: 5-10 min` stöds.
Om agenten saknar planverktyget kan den publicera ett meddelande som börjar med
`Plan:` följt av Markdown-raderna `- [ ] Steg`, `- [>] Pågående steg` och
`- [x] Färdigt steg`. Dashboarden visar checklistan i Plan-fliken och läser
senare statusuppdateringar i samma format. Den markerar inget klart på egen hand.
Planblocken döljs i Chat; vanliga svar efter checklistan och dina egna meddelanden
visas som vanligt. Hela originalmeddelandet finns kvar i den sparade historiken.

`/remove` döljer bara agenten i denna dashboard. Chatten och worktreen finns kvar,
och ett pågående arbete fortsätter. Valet sparas mellan omstarter. `/hidden`
visar de dolda agenterna så att du kan välja en och köra `/restore`.

`/archive` använder Codex eget arkiv och påverkar därför också andra Codex-klienter.
Aktiva eller väntande agenter måste först avbrytas med `/interrupt` eller bli klara.
I arkivet väljer du en chatt med piltangenterna eller musen, läser historiken och
kör `/restore` för att lägga tillbaka den i sidopanelen. Att läsa arkivet startar
ingen agenttur. Du kan även ange ett namn, exempelvis `/remove Min agent`; om
namnet matchar flera chattar behöver du välja en eller ange ett mer exakt namn.

Uppgifter skapade med CLI-kommandot `plan` börjar från projektets **incheckade HEAD** i en egen branch under
`dashboard/…`. Din vanliga branch och oincheckade ändringar lämnas orörda.
Worktrees ligger i `~/.codex-dashboard/worktrees/`. Granska och slå ihop arbetet
med Git när du är nöjd; dashboarden slår inte ihop eller tar bort worktrees.

F3 och `/new` skapar en agent med namn och modell (pil upp/ned väljer modell).
Ingen arbetsuppgift skickas automatiskt. Agenten börjar i en egen mapp under
`~/.codex-dashboard/agents/`. Välj **Setup → This agent / task → Choose folder…**
för att bläddra bland mappar och koppla en arbetsmapp. Klick eller Enter/högerpil
öppnar en mapp; vänsterpil går till föräldramappen. Välj **OK · Use this folder**
för att spara och importera profilen. Esc avbryter. Arbetsmappen och upptäckta
Git-/package.json-uppgifter visas i Setup. Scripts och portar läses utan att köras. Mappbytet gäller
nästa tur och kräver att agenten är redo.

**Setup → Access** visar senast kända filåtkomst och godkännandepolicy.
Välj filåtkomst (**Keep current**, **Read only**, **Workspace**, **Full access**),
godkännanden och nätverksåtkomst för nästa tur. Full access inkluderar nätverk;
Keep current behåller policyn. Never visar inga godkännandedialoger; med en
begränsad sandbox kan blockerade åtgärder fortfarande nekas.
Snabbvalet **Autonomous / full access** sätter Full access och Never tillsammans.
**Read only** och **Workspace + network** finns också som snabbval.
**Access explained…** beskriver alternativen. Sparade ändringar som skiljer sig
från rapporterad session markeras **Saved changes pending next turn**.
De tillämpas när nästa meddelande skickas till en redo agent. Att styra en redan
arbetande agent ändrar inte dess behörigheter och löser inte en väntande förfrågan.
Ändringarna skickas som API-inställningar, inte bara textinstruktioner.
Befintliga chattar behåller sin åtkomst med standardvalet Keep current. Modellval gäller nästa tur;
meddelanden till en redan arbetande agent ändrar inte dess pågående modell.

## Isolerat arbete med Git worktree

Efter mappval/import visas **Setup → Git worktree** när mappen tillhör ett Git-repo.
**Isolated worktree** är på som standard. **Base branch** öppnar en lista med
`main` överst, följd av andra branches. Klick eller Enter väljer; Esc avbryter.
Välj **Base branch** (tomt betyder
repoets aktuella incheckade HEAD) och skicka sedan uppgiften. Första meddelandet
skapar agentens egen worktree under `~/.codex-dashboard/worktrees/`. Nästa
meddelande återanvänder den. Ingen worktree skapas av att bara ändra Setup.

Varje agent har egen chatt, plan, bascommit och arbetsmapp. Flera agenter kan
arbeta samtidigt från olika branches i samma repo. Skapa en ny agent för ett
annat branchsammanhang. Den vanliga checkouten byter inte branch och dess
oincheckade filer kopieras inte. Mapp och bas kan inte bytas för en agent som
redan har en worktree. Mappar utan Git fortsätter som vanliga arbetsmappar.

När resultatet är klart väljer du **Local review branch** (eller automatiskt namn)
och **Prepare local branch…**. Klicket ber agenten granska och committa sitt
avsedda arbete i worktreen och skapa en lokal branch vid resultatet. Agenten ska
lämna worktreen på detached HEAD så branchen kan checkas ut i en annan checkout.
Tillfälliga worktrees kräver inget Jira-ID eller slutligt branchnamn. Du väljer
själv namnet på granskningsbranchen senare; wikins namnkonvention är en referens
för eventuell publicering, inget krav för lokal granskning. Befintliga branches skrivs inte över. Ingen push, PR eller automatisk merge ingår.
Setup visar det begärda branchnamnet; agentens svar bekräftar faktiskt resultat,
commit och utförda kontroller. Det är ett agentuppdrag, inte ett löfte om att
branchen är klar direkt när knappen trycks.

AGENTS.override.md prioriteras över AGENTS.md i samma mapp vid import. Tillämpliga
instruktioner i överordnade mappar inkluderas. Projektets egna instruktioner läses
även när de är lokala och inte följer med från Git till den nya worktreen.

## Status och bakgrundsarbete

- `working`: Codex rapporterar en aktiv tur.
- `approval` / `question`: Codex väntar på godkännande eller svar.
- `quiet (active)`: ingen observerad aktivitet under två minuter; det bevisar inte
  att agenten har fastnat.
- `idle` / `saved`: ingen aktiv tur, respektive en sparad chatt som inte är laddad.
- `offline`: anslutningen är bruten; aktuell agentstatus kan inte verifieras.

Uppgiften markeras klar när dess tur slutförs. Det ersätter inte granskning av
resultatet. Planerade uppgifter startar aldrig automatiskt.

Terminalvyn avslutas när terminalanslutningen bryts (PTY hangup) eller den får
SIGHUP/SIGTERM. Renderingsloopen begränsas även vid omedelbara tangentläsningsfel
så att en frånkopplad vy inte kan rusa på CPU. Aktiv inmatning kan uppdatera
vyn upp till 60 gånger per sekund; tomgång och läsfel begränsas till 10.
Inmatningsskurar behandlas i högst 2 ms före nästa omritning.

En separat dashboardprocess behåller anslutningen när terminalvyn stängs. På
macOS används `caffeinate -i` medan observerade agenter arbetar eller väntar.
Skärmsläckare och skärmlås tillåts. Utloggning, avstängning och stängt laptoplock
omfattas inte; dashboarden är inte installerad som en tjänst som startar vid boot.

Tillstånd sparas i `~/.codex-dashboard/state.json`, logg i `service.log`, och IPC
använder en lokal Unix-socket. Dessa filer ligger utanför projekten agenterna
arbetar i. `CODEX_DASHBOARD_HOME` eller `--state-dir` väljer en annan plats.
Codex behåller den fullständiga chatthistoriken; dashboarden cachar de senaste
250 visningsposterna per chatt och begränsar varje post till 30 000 tecken.
Terminalutmatning visas bara när du väljer verktygsfliken.

Godkännanden beviljas aldrig automatiskt. Klicka på Waiting-raden eller använd
`/request` / `/requests` för att öppna förfrågan. Ett vanligt chattmeddelande är
inte ett godkännandesvar. Förfrågevyn stöder kommandon,
filändringar, behörigheter och textfrågor. Andra typer visas med sina uppgifter
och kan besvaras med `respond` nedan. En fråga som ägs av en annan klient kan
behöva besvaras i den klienten. Inga globala Codex-säkerhetsinställningar ändras.

## CLI och felsökning

```sh
tyrell doctor
tyrell status
tyrell demo
tyrell plan --repo /path/to/project --title "Fix tests" "Investigate and fix the failing tests"
tyrell start TASK_ID
tyrell send THREAD_ID "Check the edge cases too"
tyrell respond REQUEST_ID '{"decision":"decline"}'
```

`doctor` kontrollerar bara anslutning, modellista och chattlista. `status` visar
metadata som JSON, utan meddelandehistorik. `demo` visar exempeldata och kör inga
agenter. Om Codex-tjänsten saknas, starta Codex som vanligt och öppna dashboarden
igen. Dashboarden stoppar eller startar aldrig om den gemensamma Codex-tjänsten.

Dashboardprocessen kan stoppas med `kill -TERM` och dess PID från `status`.
Avbryt först egna pågående uppgifter om du avser att avsluta dem; att stoppa
dashboardprocessen är inte ett kommando för att stoppa agenter.

För att avinstallera shell-integrationen: ta bort raden som läser in
`~/.codex-dashboard/shell.zsh` ur `.zshrc`, kör `unfunction codex`, och ta bort
`~/.local/bin/codex-dashboard`. Bevara tillstånd och worktrees tills allt arbete
är omhändertaget.

## Verifiering och implementation

```sh
python3 -B -m unittest discover -s tests -v
python3 -B scripts/check_live.py
python3 -B scripts/check_e2e.py
python3 -B scripts/check_ui.py
```

Enhetstesterna använder en simulerad app-server samt riktiga Git-worktrees,
separata processer och lokal IPC. `check_live.py` läser den sparade ursprungschatten.
`check_e2e.py` är ett uttryckligt live-test: det skapar ett temporärt Git-projekt,
en worktree och en kort Codex-tur, verifierar svaret medan klienten är frånkopplad,
och avslutar sin egen dashboardprocess. Det använder modellkapacitet och behåller
testartefakterna på den utskrivna temporära sökvägen.

`check_ui.py` provar den riktiga terminalvyn med exempeldata i en PTY, inklusive
skrollning, flikbyte, färger och stängning. Det startar inga agenter.

Gränssnittet är byggt med Python curses och tjänsten med asyncio. Den installerade
Codex-proxyn vidarebefordrar råa bytes till en Unix-socket, så anslutningen använder
WebSocket-handshake och maskerade klientramar. Protokollscheman från den lokala
CLI-versionen kan sparas lokalt i `schema/` som ignorerad utvecklingsreferens. Se även [officiell OpenAI-dokumentation om
App Server](https://learn.chatgpt.com/docs/app-server).

### Ändrade filer och fliknavigering

**Files** visar agentens rapporterade filändringar i ett utfällbart filträd. Klicka
på en fil för att se dess senaste rapporterade patch med gröna tillägg och röda
borttagningar. Diffen är valbar och är inte en sammanlagd Git-diff. Ändringar som
bara görs via shellkommandon och inte rapporteras som filhändelser kan saknas.
Pending, Failed och Declined skiljs från genomförda ändringar.

**F2** eller ett klick på en flik fokuserar flikraden. **←/→** växlar mellan
flikarna, **↓/Enter** går in i innehållet och **Esc** återgår till Chat och Prompt.
I filträdet väljer **↑/↓** en rad, **←/→** stänger/öppnar mappar och **Enter**
öppnar filens patch. Klicka på **Back to file tree** eller tryck **←** för att
återgå till trädet. Utkastet i Prompt bevaras.

AGENTS.md och agent.md i vald mapp tas med i repository guidance vid import.
Agenten instrueras att läsa filerna samt tillämpliga instruktioner ovanför och
under arbetsmappen. Setup och förhandsvisningen visar en röd **Instruction conflicts**
rubrik när importerade explicita regler avviker från valen (pakethanterare,
testkörning, lint, typkontroll eller testskapande). Det är en begränsad kontroll
av explicita svenska och engelska regelmönster, inte en fullständig semantisk analys.
AGENTS.md i överordnade mappar tas också med. Motsatta regler mellan källor
flaggas med en uppmaning att granska räckvidd och prioritet; kodblock och
villkorade regler räknas inte som ovillkorliga krav. Uppdatera
mappimporten efter ändringar i AGENTS.md. Agenten ska alltid läsa aktuell fil.

Cmd+Enter använder Codex turn/steer när agenten arbetar. Meddelandet kompletteras
med en instruktion att läsa styrningen vid nästa säkra avstämning, behålla
ofärdigt arbete och uppdatera planen. En redan körande verktygsoperation stoppas
inte. Vanlig Enter fungerar som tidigare. Ghostty använder bindningen
`keybind = super+enter=text:\x1b[13;9u`, som ersätter dess standardgenväg för
helskärm även utanför dashboarden. Ladda om med Cmd+Shift+, efter ändringen.

## Processes och agentportar

**Processes** (F2 eller `/processes`) visar processnamn, PID och TCP-portar som
lyssnar. Processer i en agents worktree märks **Workspace match** och visar agent
samt arbetsmapp. Det bevisar inte vem som startade processen. Övriga lyssnare
visas som **Unassigned**. Listan läses med lsof för den lokala användaren, utan
kommandoradsargument eller miljövariabler, och uppdateras ungefär var femte sekund
medan fliken används. Ingen process stoppas från denna vy.

Agenten får separata dev- och testportar i intervallet **42000–42999** när nästa
meddelande skickas. Upptagna portar, konfigurerade utvecklarportar och andra
agenters tilldelningar undviks. Portarna visas i Processes och Setup → Runtime &
ports. **Developer default port** förblir projektets vanliga utvecklarport.
Agenten instrueras att skriva över paketets standardport via dess stödda flagga
eller miljövariabel när en egen server får startas. Tilldelning är inte en
socketreservation: porten måste kontrolleras igen precis före start. Agenten får
inte stoppa en annan process eller falla tillbaka till utvecklarens port.
Inställningen **Development servers** avgör fortfarande om egen server får startas.
