# Setup – planera hur agenten ska arbeta

Öppna **Setup** genom att klicka på fliken eller växla med F2. `/setup` fungerar också.
Gränssnittet är på engelska. Klicka på en grupp för att öppna den; de övriga
grupperna kan vara stängda så att inställningarna går att överblicka.

Klicka på kryssrutor för att ändra dem. Val som modell och svarslängd växlar vid
klick eller med vänster/höger pil. Upp/ned väljer en rad; Enter eller mellanslag
aktiverar den. Textfält öppnas i skrivfältet nedanför. Enter sparar, Esc avbryter
och återgår till Chat. Ett tomt textfält rensar värdet. Chattutkastet bevaras.

## Tre nivåer

**Scope** växlar mellan vald agent/planerad uppgift, importerade projektprofiler
och gemensamma förval. Ordningen är:

1. Agentens egna inställningar.
2. Projektets inställningar.
3. Gemensamma förval.
4. Dashboardens grundvärden.

En ändring sparas direkt efter en kryssruta/ett val, eller efter Enter i ett textfält.
Fälthjälpen visar om värdet är ett eget val eller ärvt. **Reset this scope** tar bort
nivåns egna inställningar. **Preview agent instructions** visar det effektiva
resultatet och instruktionerna som agenten kommer att få.

Agentval finns också på ännu inte startade uppgifter från F3. När uppgiften
startas i en separat Git-worktree följer inställningarna och projektkopplingen med.

## Innehåll i första versionen

| Grupp | Inställningar |
| --- | --- |
| Model & speed | Modell, resonemangsnivå och Standard/Fast där modellen stöder det. Fast kan öka förbrukningen; beskrivningen kommer från Codex. |
| Communication | Svarslängd, språk, uppdateringsfrekvens, testresultat och länkar i svaret. Planen är alltid obligatorisk. |
| Task brief | Implementera, planera, granska eller undersöka; mål, acceptanskriterier, avgränsningar och ärende. |
| Project & references | Teknikstack, Node, pakethanterare, repository, dokumentation, preview-/rapportlänkar och egna instruktioner. |
| Tests & checks | Relevanta kontroller eller hela sviten; lint, typkontroll, unit, integration, E2E, build och redigerbara kommandon. |
| Runtime & ports | Återanvänd server eller starta en egen isolerad process, dev-/testport, explicit test-URL, miljö, installation och städning. |
| Terraform | Rotmodul, befintlig workspace, tfvars-sökvägar, fmt, validate och valbar plan. Ingen apply/destroy som del av verifiering. |
| Engineering quality | Komponentbibliotek, i18n, tillgänglighet, responsivitet, regressionstester, beroenden och granskning av slutlig diff. |
| Git & handover | Branchnamn, lokala ändringar/commit/draft PR, PR-mall och överlämningsanteckningar. |

## Projektprofiler är valfria

**Import project context** läser metadata från en projektkatalog. Importen är generell;
det finns inget krav på ett specifikt projekt eller en viss GitHub Enterprise-domän. Den läser
bland annat package.json, .nvmrc, README och relevanta konventionsfiler för att fylla
i teknikstack, kommandon och länkar. Git-origin normaliseras till en URL utan credentials.
Terraform-katalog kan identifieras i vanliga placeringar utan att läsa tfvars.

Importen startar inga paketinstallationer, tester eller servrar. `.env`, `.npmrc`,
tokenfiler och autentiseringsdata importeras inte. En ny import bevarar profilens
befintliga inställningar. Värden går att redigera eller rensa efteråt.

Projektprofiler matchas mot projektkatalogen. En worktree som dashboarden skapar
behåller kopplingen till ursprungsprojektet. En befintlig extern worktree behöver
en profil för sin katalog om den saknar denna koppling.

En projektprofil kan innehålla React/TypeScript/Vite, ett komponentbibliotek,
Biome, Vitest, Playwright, npm, GitHub Enterprise och HTTPS. Dessa värden tillhör
projektprofilen och påverkar inte andra projekt eller gemensamma förval.

## När valen börjar gälla

Att spara en inställning startar ingen uppgift och skickar inget nytt chattmeddelande.
Arbetspreferenserna bifogas nästa meddelande, även om det styr en pågående agent.
Modell, resonemang och hastighet skickas som Codex-inställningar när nästa nya turn
startar. De ändrar inte modellen mitt i en pågående turn.

Övriga val är arbetsinstruktioner till agenten. De ändrar inte Codex eller operativsystemets
behörigheter och är ingen teknisk process-/portspärr. Explicit uppgiftsbeskrivning och
repositoryregler har företräde. Testresultat ska redovisa vad som faktiskt kördes.

Modellalternativen hämtas från det anslutna Codex-kontots modellkatalog, enligt
[OpenAI:s App Server-dokumentation](https://learn.chatgpt.com/docs/app-server#models).
Dashboarden validerar vald modell, resonemangsnivå och hastighet mot den katalogen.

Inställningarna sparas i dashboardens lokala `state.json`, med samma filbehörigheter
och atomiska skrivning som övrig state. Repositoryfiler och Codex globala config
skrivs inte om. Agentinställningar följer agenten genom arkivering/återställning.

## Git worktree

Git-projekt får **Isolated worktree** på som standard. **Base branch** väljer
startpunkt innan första uppgiften. Varje agent får egen arbetsmapp och återanvänder
den över meddelanden. En ny agent ger ett separat branchsammanhang.

**Prepare local branch…** ber agenten granska och committa sitt arbete lokalt och
skapa **Local review branch**. Användarens checkout lämnas orörd. Agentens svar
bekräftar resultatet; knappen startar ett uppdrag och gör ingen push eller merge.
