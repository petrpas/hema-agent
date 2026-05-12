#set page(paper:"a4", margin: 1.25cm)
#set text(font: "GFS Neohellenic", size: 12pt)

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
    left: if x == 0 or x == 1 or x == 9 { 1.5pt } else {0.5pt},
    right: if x == 12 { 1.5pt } else {0.5pt},
    bottom: 1.5pt,
  ),
)

#table(
  align: (left, center, center, center, center, center, center, center, center, center, center, center, center),
  columns: (6fr, 1.25fr, 1.25fr, 1.25fr, 1.25fr, 1.25fr, 1.25fr, 1.25fr, 1.25fr, 1fr, 1fr, 1fr, 1fr),
  inset: (top: 5pt, bottom: 5pt),
  table.header(
    table.cell(align: center, [*Fencer*]),
    table.cell(align: center, [*{{f1}}*]),
    table.cell(align: center, [*{{f2}}*]),
    table.cell(align: center, [*{{f3}}*]),
    table.cell(align: center, [*{{f4}}*]),
    table.cell(align: center, [*{{f5}}*]),
    table.cell(align: center, [*{{f6}}*]),
    table.cell(align: center, [*{{f7}}*]),
    table.cell(align: center, [*{{f8}}*]),
    table.cell(align: center, [*Win*]),
    table.cell(align: center, [*TS*]),
    table.cell(align: center, [*TR*]),
    table.cell(align: center, [*Idx*]),
  ),
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]], [------],[],[],[],[],[],[],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]], [],[-----],[],[],[],[],[],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]], [],[],[-----],[],[],[],[],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]], [],[],[],[-----],[],[],[],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]], [],[],[],[],[-----],[],[],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_6}}]], [],[],[],[],[],[-----],[],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_7}}]], [],[],[],[],[],[],[-----],[],[],[],[],[],
  box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_8}}]], [],[],[],[],[],[],[],[-----],[],[],[],[],
)
#v(-0.8em)
#align(right)[#text(size: 8pt)[Fencers should sign the results after the pool is finished. #h(1fr) Win -- matches won, TS -- touches scored, TR -- touches recieved, Idx = TS $-$ TR]]

#v(0.4cm)


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
  inset: (top: 4pt, bottom: 4pt),
  table.header(
    table.cell(align: center, [*Match*]),
    table.cell(align: center, [*Fencer Left*]),
    table.cell(align: center, [*Fencer Right*]),
    table.cell(align: center, [*Score*]),
    table.cell(align: center, [*Note*]),
  ),
  [1],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_8}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],[],[],
  [2],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_7}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],[],[],
  [3],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_6}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],[],[],
  [4],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],[],[],
  [5],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_8}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],[],[],
  [6],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],[],[],
  [7],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_7}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],[],[],
  [8],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_6}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],[],[],
  [9],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_8}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],[],[],
  [10],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],[],[],
  [11],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],[],[],
  [12],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_7}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_6}}]],[],[],
  [13],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_8}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],[],[],
  [14],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],[],[],
  [15],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_6}}]],[],[],
  [16],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_7}}]],[],[],
  [17],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_8}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],[],[],
  [18],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_6}}]],[],[],
  [19],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_7}}]],[],[],
  [20],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],[],[],
  [21],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_8}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_6}}]],[],[],
  [22],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_7}}]],[],[],
  [23],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],[],[],
  [24],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],[],[],
  [25],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_8}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_7}}]],[],[],
  [26],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_6}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_1}}]],[],[],
  [27],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_5}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_2}}]],[],[],
  [28],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_4}}]],box(width: 100%, height: 0.85em, clip: true)[#nowrap[{{fencer_3}}]],[],[],
)
