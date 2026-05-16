#set page(paper: "a4", margin: 1.5cm, columns: 2)
#set text(font: "GFS Neohellenic", size: 13pt)
#set table(
  stroke: (x, y) => if y == 0 {
    (bottom: 0.7pt + black)
  },
  align: (x, y) => (
    if x > 0 { center }
    else { left }
  )
)

== Prohlášení účastníka turnaje

#v(1em)

*Já*, níže podepsaný *účastník* HEMA turnaje \ *{{tournament}}* pořádaného Duelanty od sv. Rocha v Praze dne {{date}} *prohlašuji*, že jsem seznámen/a s pravidly turnaje a bezpečnostními pokyny a zavazuji se je dodržovat.

=== Zdravotní způsobilost

- Jsem zdravotně způsobilý/á k účasti na fyzicky i psychicky náročné sportovní aktivitě.
- Netrpím žádnou zdravotní komplikací, která by představovala zvýšené riziko pro mne nebo ostatní účastníky turnaje.
- V případě jakéhokoli zranění, nevolnosti či jiných zdravotních okolností ihned upozorním pořadatele a vyhledám pomoc zdravotníka.
- Respektuji případné rozhodnutí pořadatele o dočasném či trvalém vyloučení z turnaje ze zdravotních či bezpečnostních důvodů.

=== Ochranné vybavení

- Budu po celou dobu turnaje používat ochranné vybavení předepsané pravidly turnaje a schválené pořadateli turnaje. Ručím za jejich bezvadný stav v průběhu celého turnaje.
- V případě poškození vybavení během turnaje ihned uvědomím pořadatele. Do zjednání nápravy nebudu pokračovat v boji.

=== Zbraně

- Používám pouze zbraně, které splňují pravidla dané disciplíny, prošly kontrolou pořadatele a nejeví známky poškození, jež by mohlo ohrozit bezpečnost.
- V případě poškození zbraně během turnaje ihned uvědomím pořadatele a nebudu takovou zbraň dále používat.

#colbreak()

=== Chování na ploše

- Budu se po celou dobu turnaje řídit pokyny rozhodčích a přadatelů.
- Vstoupím do prostoru vyhrazeného boji pouze tehdy, když k tomu budu vyzván/a.
- Budu se chovat sportovně a s respektem vůči soupeřům, rozhodčím a ostatním účastníkům.
- Vyhnu se použití nadměrné síly, hrubosti či akcí vedoucích ke zranění soupeře.

=== Zodpovědnost

- Beru na vědomí, že účast na turnaji je na vlastní riziko. Jakkoli se všichni účastníci maximálně snaží o bezpečný průběh akce, HEMA je kontaktní bojový sport a riziko zranění či smrti nelze nikdy zcela odstranit. Jsem si toho vědom a jsem s tím smířen.
- Pořadatelé nenesou odpovědnost za škody na majetku či zdraví utrpěné během turnaje.

=== Souhlas se zpracováním údajů

- Souhlasím se zpracováním osobních údajů v rozsahu nutném pro uspořádání turnaje.
- Souhlasím s publikací výsledků turnaje na Hema Ratings a Českého HEMA žebříčku. Na pozdější změnu názoru nebude brán zřetel.
- Souhlasím s tím, že pořadatel i ostatní účastníci turnaje si mohou pořizovat audiovizuální záznam soubojů pro svou vlastní potřebu a pořadatel navíc za účelem propagace HEMA sportu a souvisejících akcí.

=== Souhlas s vyloučením

- Vím, že v případě opakovaného či závažného porušování pravidel či zvyklostí mohu být z turnaje bez náhrady vyloučen/a.

#pagebreak()

== Tournament Participant Declaration

#v(1em)

*I*, the undersigned *participant* of the HEMA tournament *{{tournament}}*, held on {{date}}, organized by Duelanti od sv. Rocha and Akademie Rytířských umění in Prague, *declare* that I am familiar with the tournament rules and safety instructions and I agree to abide by them.

=== Medical Fitness

- I am physically and mentally fit to take part in a demanding sports activity.
- I do not suffer from any medical condition that would pose an increased risk to myself or to other tournament participants.
- In the event of any injury, illness, or other health-related issue, I will immediately inform the organizers and seek medical assistance.
- I respect the organizer’s right to temporarily or permanently exclude me from the tournament for health or safety reasons.

=== Protective Equipment

- I will use protective equipment required by the tournament rules and approved by the organizers throughout the event. I take full responsibility for the proper condition of this equipment during the tournament.
- If my equipment becomes damaged during the event, I will immediately inform the organizers and will not continue fighting until it is resolved.

=== Weapons

- I will only use weapons that comply with the rules of the given discipline, have passed inspection by the organizers, and show no signs of damage that could compromise safety.
- If a weapon is damaged during the tournament, I will immediately inform the organizers and will not use the weapon further.

=== Behavior on the Field

- I will follow the instructions of referees and organizers at all times during the tournament.
- I will only enter the fighting area when explicitly invited to do so.
- I will behave in a sportsmanlike manner and show respect to my opponents, referees, and fellow participants.
- I will avoid excessive force, rough behavior, or any actions that could lead to injury of an opponent.

=== Responsibility

- I understand that participation in the tournament is at my own risk. While all participants strive to ensure a safe event, HEMA is a contact combat sport and the risk of injury or death can never be completely eliminated. I acknowledge and accept this risk.
- The organizers are not liable for any property damage or personal injury sustained during the tournament.

=== Consent to Data Processing

- I consent to the processing of personal data to the extent necessary for the organization of the tournament.
- I agree to the publication of tournament results on HEMA Ratings and the Czech HEMA ranking. Changes of opinion at a later date will not be taken into account.
- I agree that the organizer and other tournament participants may record audiovisual footage of the fights for their personal use, and that the organizer may also use such footage for the promotion of the HEMA sport and related events.

=== Consent to Exclusion

- I understand that repeated or serious violations of the rules or tournament norms may result in my exclusion from the tournament without compensation.

#pagebreak()
#set page(paper: "a4", columns: 2, margin: 2em)
#set text(size: 13pt)

#place(
  top + center,
  scope: "parent",
  float: true,
  text(1em, weight: "bold")[
    V Praze dne {{date}} #h(1fr) In Prague on {{date}}],
)


#table(
  columns: (2em, 3fr, 2fr, 2fr),
  inset: (x: 0.5em, y: 0.75em),
  align: left,
  table.header(
    [*n.*], [*Jméno \ Name*], [*Narozen\ Birth date*], [*Podpis \ Signature*],
  ),
  {{rows}}
)
