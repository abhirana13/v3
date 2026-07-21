# Claude Design prompts — Dashboards (one per screen)

How to use: paste one prompt at a time into the design tool, and attach the matching screenshot(s).
Every prompt asks for **React function components, Tailwind, dumb (props in / callbacks out)** so
Claude Code can wire them to the API later without rework. The dummy data below matches the backend
shapes in `dashboards-build-plan.md`, so what you design maps 1:1 to what the API will return.

Shared data shapes (referenced by the prompts):
```js
// number widget
{ id:'w2', type:'number', name:'Android Rev/Day', asOf:"D-2 | Jul 18'26",
  displayValue:'$ 24.63K',
  compares:[ {label:'vs previous day', pct:-0.688, abs:-170.83, dir:'down'},
             {label:'vs last week',    pct:11.168, abs:2470,  dir:'up'} ],
  target:null }
// chart widget
{ id:'w1', type:'chart', name:'Daily Revenue (May Target: $24K)', viz:'line',
  asOf:"D-2 | Apr 21'26 to Jul 18'26", target:24000,
  series:[{ name:'revenue', axis:'primary',
            points:[{t:'2026-04-21',v:35000},{t:'2026-05-05',v:44000}, /* ...daily... */ {t:'2026-07-18',v:47000}] }] }
// grid layout item (react-grid-layout)
{ i:'w1', x:0, y:0, w:6, h:8 }   // number tiles ~ w:3,h:4 ; charts ~ w:6,h:8
// global filter chip
{ dimension:'country', label:'country', selected:['US','GB','CA','AU'], allSelected:false }
```

═══════════════════════════════════════════════════════════════════════════════════
## SCREEN 1 — Dashboard View (read mode)   [attach screenshot 1 + the Engagement tab shot]
═══════════════════════════════════════════════════════════════════════════════════
Build a **React dashboard view** for a game-analytics tool. Use the attached screenshot as
layout reference (match structure, not pixels). Static dummy data only; all data via props,
all actions via callbacks. Use **react-grid-layout** for the widget grid.

Layout:
- **Header:** dashboard title + number (e.g. "Word Connect Association: Scrum  #155"), an "Enabled"
  badge, and Share / Replicate / Edit Dashboard buttons on the right.
- **Tab bar:** "Main", "ROAS Deepdive", "Engagement Deepdive" (clickable tabs; active underlined).
- **Controls row:** a small formula chip (`fx: M`), and on the right a granularity selector (Day)
  + date-range picker (`2026/04/21 → 2026/07/20`).
- **Global filter bar:** chips per dimension — `All`, `gid`, `cli`, `country`, `install_type` —
  each a multi-select dropdown showing the dimension + a count/`All`, plus an `Apply` button.
- **Widget grid (react-grid-layout, read-only positions in this screen):** a responsive grid of:
  - **NumberWidget** tiles: title + tiny as-of label; a large value (e.g. `$ 24.63K`, `64.72K`);
    below it one or two comparison rows — a colored pill with the % (green ▲ / red ▼) + the label
    ("vs previous day", "vs last week") + the absolute delta on the right (e.g. `-170.83`, `+2.47K`).
  - **ChartWidget** cards: title (may include a target, e.g. "Daily Revenue (May Target: $24K)"),
    as-of label, and a line chart (use a charting lib; ECharts or Recharts). If `target` is set,
    draw a horizontal target line.
  - Each widget card has a filter icon, an expand/fullscreen icon, and an info icon in its corner.

Components: `<DashboardView>`, `<DashboardHeader>`, `<TabBar>`, `<GlobalFilterBar>`,
`<FilterChip>`, `<WidgetGrid>` (react-grid-layout), `<NumberWidget>`, `<ChartWidget>`.
Callbacks: `onTabChange`, `onFilterChange`, `onApply`, `onDateRangeChange`, `onWidgetExpand`,
`onEditDashboard`.
Render with ~6–8 widgets: one big revenue line chart + Android/iOS Rev/Day, Android/iOS DAU number
tiles (use the dummy shapes above). Light theme, dense, modern.
Do NOT build: auth, editing/drag, the edit-widget modal (separate screens).

═══════════════════════════════════════════════════════════════════════════════════
## SCREEN 2 — Dashboard Edit Mode   [attach the "Edit Mode" screenshot]
═══════════════════════════════════════════════════════════════════════════════════
Build the **edit-mode** version of the dashboard view above. Same grid and widgets, but now
**editable via react-grid-layout**: widgets are **draggable** (show a drag handle `⠿`) and
**resizable** (show resize handles on cards). Static dummy data; props in, callbacks out.

Differences from read mode:
- Header shows **"Edit Mode"**, an Enabled toggle, and **Discard** / **Save Dashboard** buttons.
- A toolbar row with **"Add Widget"** (dropdown → Chart / Number — only these two in v1) and a
  **"+ Filter"** button, alongside the same global filter chips.
- Tabs show an edit affordance (a `…`/menu per tab and a `+` to add a tab).
- Each widget card shows a **gear** (opens settings) and a **`…`** menu (duplicate / delete), plus
  the drag handle and resize handles.
- Dragging/resizing updates the layout; emit `onLayoutChange(layout)` with the react-grid-layout
  array. Nothing persists in the mock — just call the callback.

Components (reuse Screen 1's widgets): `<DashboardEditView>`, `<EditToolbar>`,
`<AddWidgetMenu>`, `<EditableWidgetGrid>` (react-grid-layout with `isDraggable`/`isResizable`),
`<WidgetChrome>` (drag handle + gear + … menu wrapper).
Callbacks: `onAddWidget(type)`, `onLayoutChange`, `onWidgetSettings(id)`, `onWidgetDelete(id)`,
`onAddTab`, `onRenameTab`, `onReorderTab`, `onSave`, `onDiscard`.

═══════════════════════════════════════════════════════════════════════════════════
## SCREEN 3 — Edit Widget modal   [attach the 3 "Edit Widget : Line" screenshots]
═══════════════════════════════════════════════════════════════════════════════════
Build the **Edit Widget modal** (a dialog). Left side has a section nav: **Basic / Metrics /
Dimensions / Other Settings**; right side scrolls through those sections. Static dummy data;
props in, callbacks out; Cancel / Apply buttons in the footer.

Sections:
- **Basic:** *Source Chart* (searchable select, e.g. "chart_579 : External Net Revenue and DAU by
  Source"), *Widget Name* (text), *Visualization* (icon toggle: line / bar — v1 only these two).
- **Metrics:** a repeatable row of `metric select` + `axis select` (Primary/Secondary) + remove ✕,
  and an **"Add (n/5)"** button (max 5). Dummy: `revenue → Primary`.
- **Dimensions:**
  - *Filter By:* repeatable rows of `dimension select` + a multi-select of values (chips like
    `Android ✕`, `+1 …`, `US : United… ✕`, `+135 …`) + remove ✕, and an **Add** button.
  - *Group By (n/5):* a dimension multi-select (max 5).
- **Other Settings:** *Offset* (number) + mode select ("Only on end date"); *X-Axis* select (`time`);
  *Y-Axis Range* (Primary Y-Axis / Secondary Y-Axis controls); optional *Target* (number).

Note: this modal serves the **chart** widget. For a **number** widget, show only Basic (source
chart, name), a single *Metric* select, *Filter By*, and Other Settings with *decimals*, *unit*,
*compares* (checkboxes: vs previous day, vs last week) and *Target* — hide Visualization / Group By /
axes. Drive which fields show off a `widgetType` prop (`'chart'` | `'number'`).

Components: `<EditWidgetModal>`, `<SectionNav>`, `<MetricRow>`, `<FilterByRow>`, `<GroupBySelect>`,
`<OtherSettings>`. Callbacks: `onChange(config)`, `onApply`, `onCancel`.

═══════════════════════════════════════════════════════════════════════════════════
## SCREEN 4 — Dashboard List / Home   [no screenshot; simple]
═══════════════════════════════════════════════════════════════════════════════════
Build a simple **dashboard list**: a searchable table/cards of dashboards showing name + number,
Enabled state, last-updated, and actions (Open, Replicate). A "New Dashboard" button top-right.
Static dummy list of ~6 dashboards. Props in, callbacks out (`onOpen`, `onReplicate`, `onNew`,
`onSearch`). Match the light, dense style of the other screens.

───────────────────────────────────────────────────────────────────────────────────
Build rules for ALL screens (repeat to the tool if needed):
- React function components + Tailwind utility classes.
- **Dumb components:** data via props, user actions via callback props. No fetching, no global
  state, no data model invented inside components — consume the shapes above.
- Use **react-grid-layout** for the widget grid (screens 1–2). Charts via a charting lib.
- v1 widget types are **Chart and Number only**. Do NOT build Table/Text widgets, auth/login,
  sharing internals, or per-user layouts.
