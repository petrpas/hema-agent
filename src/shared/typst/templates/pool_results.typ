#set page(paper:"a4", margin: 1.25cm)
#set text(font: "GFS Neohellenic", size: 15pt)

#show table.header: set text(weight: "bold")

#let nowrap(body) = {
  show " ": sym.space.nobreak
  body
}

#place(
  top + center,
  scope: "parent",
  float: true,
  text(1.4em, weight: "bold")[
    {{tournament}} #h(1fr) {{discipline}} -- Order after Pools
  ],
)

#set table(
  stroke: (x, y) => if y == 0 {
    (bottom: 0.7pt + black)
  },
  align: (x, y) => (
    if x > 0 { center }
    else { left }
  )
)


#table(
  columns: (2em, 1fr, 1.4fr, 3em, 2.5em, 1em, 1.5em, 1em, 1.5em),
  align: (right, left, left, center, right, center, right, center, right),
  table.header(
    table.cell(align: center, [*No.*]),
    table.cell(align: center, [*Fencer*]),
    table.cell(align: center, [*Club*]),
    table.cell(align: center, [*Wins*]),
    table.cell(align: center, [*Index*]),
    table.cell(align: center, []),
    table.cell(align: center, [*TS*]),
    table.cell(align: center, []),
    table.cell(align: center, [*TR*])
  ),
    {{table_content}}
    // format of the lines
    // [1],	[Django Crowe],	[TWERCHHAU],	[*4* / *4*],	[*+19*],[=],[20],[−],[1],
    // use plus sign for positive index and use utf minus sign U+2212 before negative index and in the minus column
)