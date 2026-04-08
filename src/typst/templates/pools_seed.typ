#set page(margin: 2em, width: 540pt, height: auto)
#set text(font: "GFS Neohellenic", size: 14pt)

#show table.header: set text(weight: "bold")


#place(
  top + center,
  scope: "parent",
  float: true,
  text(1.4em, weight: "bold")[
    {{tournament_name}} Pools {{discipline_name}}
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

// pools: flat array of (pool_no, fencers) pairs,
// where fencers is an array of name strings sorted by seed.
// col_count: number of columns in the grid (1, 2, or 3).
{{data}}

#let render-pool(pool_no, fencers) = {
  table(
    align: (right, left),
    columns: (1.5em, 1fr),
    table.header(
      table.cell(align: center, [*Pool no. #pool_no*], colspan: 2)
    ),
    ..range(fencers.len()).map(i => {
      let name = fencers.at(i)
      ([#(i + 1).], [#name])
    }).flatten()
  )
}

#grid(
  columns: (1fr,) * col_count,
  column-gutter: 1.5em,
  row-gutter: 1em,
  ..pools.map(pool => render-pool(pool.at(0), pool.at(1)))
)
