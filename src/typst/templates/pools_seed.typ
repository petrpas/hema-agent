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

// waves: array of waves, matching PoolConfig.wave_sizes.
// Each wave is an array of (pool_no, fencers) pairs,
// where fencers is an array of name strings sorted by seed.
// Example for wave_sizes=[4,4,2,2]:
//   waves = (
//     ((1, ("A","B","C")), (2, ("D","E")), (3, (...)), (4, (...))),
//     ((5, (...)), ...),
//     ...
//   )
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

#for (wave_i, wave) in waves.enumerate() {
  columns(wave.len())[
    #for (i, pool) in wave.enumerate() {
      if i > 0 { colbreak() }
      render-pool(pool.at(0), pool.at(1))
    }
  ]
  if wave_i < waves.len() - 1 { v(1em) }
}