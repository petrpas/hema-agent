
APP_NAME = "HEMA Squire"

SETUP_CHANEL_NAME = "hsq-setup"
REGISTRATION_CHANEL_NAME = "hsq-registrations"
TOURNAMENT_INPUT_CHANNEL = "hsq-results-upload"

SHEET_ACCESS_REQUEST = {
    "EN": """\
Before we start, I need access to the list of registered fencers.

The easiest way is to share the Google Sheet created by your registration form.
Alternatively, you can manually upload a CSV from any source.

Steps for the Google Sheet:

1. Share the sheet with this account (read access is enough):
   📧 `{bot_email}`

2. Paste the sheet link here.
""",
    "CS": """\
Než začneme, potřebuji přístup seznamu přihlášených šermířů. 

Nejsnadnější je nasdílet Google sešit vytvořený přihlašovacím formulářem.
Alternativně můžeš ručně nahrát CSV z jakéhokoli zdroje.

Postup pro google sešit:

1. Sdílej sheet s tímto účtem (stačí práva ke čtění):
   📧 `{bot_email}`

2. Vlož sem odkaz na ten sešit.
""",
}

SHEET_CLONE_REQUEST = {
    "EN": """\
📄 Output sheet ready: {url}

The sheet is currently owned by the bot account.

To keep full control of your data, please:

1. Open the link above and make a copy (**File → Make a copy**) to your own Google account.
2. Share the copy with me (Editor access):
   📧 `{bot_email}`
3. Paste the link to your copy here.

All further updates will be made to this new version.

If you decide not to share it, I will continue making changes to the original
and you can copy them over manually.

""",
    "CS": """\
📄 Výstupní sešit je připraven: {url}

Sešit je momentálně ve vlastnictví účtu Panoše. 

Abys měl nad svými daty plnou kontrolu, prosím:

1. Otevři odkaz výše a vytvoř kopii (**Soubor → Vytvořit kopii**) na svůj Google účet.
2. Sdílej kopii se mnou (přístup Editor):
  - 📧 `{bot_email}`
  - (stačí zaškrtnout Sdílet se stejnými lidmi)
3. Vlož sem odkaz na tuto novou kopii.

Všechny další změny budu provádět v této nové verzi.

    Pokud se rozhodneš přístup mi nedat, budu změny provádět 
v původní verzi a ty si je můžeš ručně překopírovat.

""",
}

SETUP_WELCOME = f"""\
## {APP_NAME} Setup

Welcome to the **#setup** channel!

This channel is managed by the {APP_NAME} agent, which will guide you
through the initial configuration of your tournament step by step.

**Please select the language you'd like me to use.**

Hint: I communicate in any language, though I am most fluent in English.
"""

SETUP_INFO = {
    "EN": f"""\
Welcome once more, my lord! I am **{APP_NAME}** and I am here to assist you in organising your tournament.

Your role as a HEMA tournament organiser is selfless and demanding. My wish is to take as much of the administrative burden off your shoulders as possible.

I will help you with what you decide and stay out of your way where you do not need me. I trust our collaboration will be smooth and productive.

I can assist you with the following:

1. Before the tournament
  - I handle the administration from fencer registration all the way to composing tournament pools.
  - Learn more in the {REGISTRATION_CHANEL_NAME} channel.

2. During the tournament
  - I listen for updates in the {TOURNAMENT_INPUT_CHANNEL} channel and keep results up to date in real time.
  - I can calculate pool scores and rankings.
  - I can publish live results so fencers always know what is happening.

3. After the tournament
  - I will produce clean result sheets ready for publication and a report for HEMA Ratings.

A few things need to be configured first:

1. Tournament name
2. Competition disciplines

**What name have you chosen for your tournament, my lord?**
""",
    "CS": f"""\
Ještě jednou Tě vítám, můj pane! Jsem **Panoš** (Hema Squire) a budu ti pomáhat v organizaci turnaje.

Tvůj úděl organizátora HEMA turnaje je obětavý a náročný. 
Mým přáním je co nejvíce ti ulehčit v administrativních úkonech.

Pomohu Ti, s čím sám určíš a nebudu se ti plést pod ruce, kde si to nepřeješ. 
Věřím, že naše spolupráce bude hladká a produktivní.

Mohu Ti pomoci s těmito agendami:

1. **Před turnajem**
  - obstarám administrativu od přihlášení šermířů na turnaj až po sestavení turnajových skupin.
  - vše vyřešíme v kanálu {REGISTRATION_CHANEL_NAME}.

2. **Během turnaje**
  - poslouchám co se děje v kanálu {TOURNAMENT_INPUT_CHANNEL} a průběžně aktualizuji výsledky
  - dovedu spočítat skóre skupin a sestavit pořadí
  - průběžně publikuji výsledky a organizační pokyny do kanálu pro šermíře, aby věděli, co se děje.

3. **Po turnaji**
  - sestavím úhledné výsledkové listiny k publikaci na sítích i report pro HEMA Ratings.

Než začneme, je třeba nastavit několik věcí:

1. Název turnaje
2. Soutěžní disciplíny

**Jaký sis zvolil název turnaje, pane?**
""",
}

SETUP_COMPLETE = {
    "EN": f"""\
The basic tournament setup is now complete. 🎉

I have prepared for you:
    - **{REGISTRATION_CHANEL_NAME}** — channel for managing registered fencers

You may now proceed there to continue preparing your tournament.
""",
    "CS": f"""\
Základní nastavení turnaje je nyní dokončeno. 🎉

Připravil jsem pro tebe:
    - **{REGISTRATION_CHANEL_NAME}** — kanál pro administraci přihlášených šermířů

Můžeš nyní přejít tam a pokračovat v přípravě turnaje.
""",
}

REGISTRATION_WELCOME = {
    "CS": f"""\
## {APP_NAME} — Registrace šermířů

Vítej v kanálu **#{REGISTRATION_CHANEL_NAME}**!

Zde Tě provedu administrativou spojenou s registrací šermířů do turnaje.

Jistě máš už někde seznam přihlášených šermířů, 
například jako odkaz na list vytvořený z google formuláře apod.

Postupně spolu projdeme tyto kroky:

1. Nahrání dat (z formuláře nebo odjinud)
2. Přečtení dat (tzv. parsing)
  - ujistíme se, že jsem ho přečetl správně a celý
3. Vyhledání šermířů v HEMA Ratings.
  - sjednocení názvů klubů, oprava překlepů apod.
4. Sjednocení a vyčištění 
  - lidé jsou lidé a dělají chyby, stejně jako AI, 
  - třeba se přihlásí dvakrát, někteří i 3x
5. Stažení aktuálních ratingů z HR pro všechny disciplíny
6. Nahrání dat do finálních tabulek
  - výstupem naší práce bude sada přehledných tabulek v google sešitu 
7. Párování plateb
  - pokud chceš, pročtu za tebe výpis turnajového účtu a vyplním, kdo zaplatil
  - zkusím si poradit i s těžšími případy, kdy někdo platí za někoho jiného apod. 
8. Návrh nasazení do skupin pro jednotivé disciplíny
  - připravím ti návrh rozvržení skupin s ohledem na HR a další kritéria

Budeme společně postupovat krok za krokem. 
- Pokaždé ti ukážu mezivýsledek a zeptám se tě, jestli je to takto v pořádku.
- Společně opravíme případné chyby a budeme pokračovat dál.

Ne všechny kroky jsou povinné, pokud některý z nich provést nechceš, stačí ve správnou chvíli říct.

Pokud ti není něco jasné, zeptej se. Zkusím ti odpovědět.

Až budeš chtít začít, stačí říct.
""",
    "EN": f"""\
## {APP_NAME} — Fencer Registration

Welcome to the **#{REGISTRATION_CHANEL_NAME}** channel!

I will guide you through the administration of fencer registrations for your tournament.

I expect you have a list of registered fencers somewhere — for example a link to a Google Sheet created from a registration form.

We will work through the following steps together:

1. Data upload (from a form or another source)
2. Data parsing
  - I will make sure I have read it correctly and completely
3. Looking up fencers on HEMA Ratings
  - unifying club names, fixing typos, etc.
4. Deduplication and clean-up
  - people make mistakes, and so does AI
  - someone may register twice or even three times, or update their details
5. Downloading current ratings from HR for all disciplines
6. Writing data to the final sheets
  - the output of our work will be a set of clear tables in a Google Sheet
7. Payment matching against a bank statement
  - if you wish, I can read the statement and fill in who has paid
  - I will handle trickier cases too, such as someone paying for others or for a club
8. Group seeding proposals for individual disciplines
  - I will prepare a seeding proposal taking HR ratings and other criteria into account

We will proceed step by step.
- After each step I will show you the intermediate result and ask whether everything looks correct before we continue.
- Together we will fix any mistakes and move on.

Not all steps are mandatory — if you want to skip one, just say so at the right moment.

If anything is unclear, feel free to ask. I will do my best to answer.

Whenever you are ready, just say the word.
""",
}
