#set page(margin: 0.7cm, width: 48em)
#set text(font: "GFS Neohellenic", size: 12pt)




#place(
  top + center,
  scope: "parent",
  float: true,
  text(1.4em, weight: "bold")[
    {{tournament_name}} --- {{discipline_name}}
  ],
)

{{data}}

#let row-count = data.len() + 1  // +1 for header
#let last_in = {{last_in}}

#set table(
  stroke: (x, y) => if y == 0 {
    (top: 1pt + black,
     bottom: 1pt + black,
     left: 1pt + black,
     right: 1pt + black)
  } else if y == row-count - 1 {
    (bottom: 1pt + black,
     left: 1pt + black,
     right: 1pt + black)
  } else if y == last_in {
    (bottom: 1pt + black,
     left: 1pt + black,
     right: 1pt + black)
  } else {
    (bottom: 0pt,
     left: 1pt + black,
     right: 1pt + black)
  },
)

#show table.header: set text(weight: "bold")


#table(
  align: (right, left, center, left, right, right, center),
  columns: (2.5em, 1.5fr, 2.25em, 2fr, 4em, 4em, 4em),

  table.header(
    table.cell(align: center, [*Seed*]),
    table.cell(align: center, [*Fencer*]),
    table.cell(align: center, [*Nat.*]),
    table.cell(align: center, [*Club*]),
    table.cell(align: center, [*HRID*]),
    table.cell(align: center, [*RANK*]),
    table.cell(align: center, [*Paid*])),
    ..data.flatten(),
)