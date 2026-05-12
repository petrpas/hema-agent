#set page(paper:"a4", margin: 1.5cm)
#set text(font: "GFS Neohellenic", size: 14pt)

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
    {{tournament}} #h(1fr) {{discipline}} -- pool no. {{pool_no}}
  ],
)

#v(2em)

== Scores

#set table(
  stroke: (x, y) => if y == 0 {
    (top: 1.5pt + black)
  } else {
    (top: 1pt + black)
  }
)

#set table(
  stroke: (x, y) => (
    top: if y == 0 or y == 1 {1.5pt} else {0.5pt} ,
    left: if x == 0 or x == 1 or x == 6 { 1.5pt } else {0.5pt},
    right: if x == 9 { 1.5pt } else {0.5pt},
    bottom: 1.5pt,
  ),
)

#table(
  align: (left, center, center, center, center, center, center, center, center, center),
  columns: (6fr, 1.25fr, 1.25fr, 1.25fr, 1.25fr, 1.25fr, 1fr, 1fr, 1fr, 1fr),
  inset: (top: 10pt, bottom: 10pt),
  table.header(
    table.cell(align: center, [*Fencer*]),
    table.cell(align: center, [*{{f1}}*]),
    table.cell(align: center, [*{{f2}}*]),
    table.cell(align: center, [*{{f3}}*]),
    table.cell(align: center, [*{{f4}}*]),
    table.cell(align: center, [*{{f5}}*]),
    table.cell(align: center, [*Win*]),
    table.cell(align: center, [*TS*]),
    table.cell(align: center, [*TR*]),
    table.cell(align: center, [*Idx*]),
  ),
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]], [------],[],[],[],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]], [],[-----],[],[],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]], [],[],[-----],[],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]], [],[],[],[-----],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]], [],[],[],[],[-----],[],[],[],[],
)
#v(-0.8em)
#align(right)[#text(size: 8pt)[Fencers should sign the results after the pool is finished. #h(1fr) Win -- matches won, TS -- touches scored, TR -- touches recieved, Idx = TS $-$ TR]]

#v(2em)

== Matches

#set table(
  stroke: (x, y) => (
    top: if y == 0 or y == 1 {1.5pt} else {0.5pt} ,
    left: if x == 0 {1.5pt} else {0.5pt},
    right: 1.5pt,
    bottom: 1.5pt,
  ),
)

#table(
  align: (center, left, left, center, center, center),
  columns: (4em, 4fr, 4fr, 1.5fr, 3fr),
  inset: (top: 10pt, bottom: 10pt),
  table.header(
    table.cell(align: center, [*Match*]),
    table.cell(align: center, [*Fencer Left*]),
    table.cell(align: center, [*Fencer Right*]),
    table.cell(align: center, [*Score*]),
    table.cell(align: center, [*Note*]),
  ),
  [1],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],[],[],
  [2],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],[],[],
  [3],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],[],[],
  [4],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],[],[],
  [5],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],[],[],
  [6],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],[],[],
  [7],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],[],[],
  [8],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],[],[],
  [9],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],[],[],
  [10],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],[],[],
)
