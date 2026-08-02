# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-08-02
- Primary product surfaces: Streamlit sidebar data-loading flow, main analysis tabs, Excel export.
- Evidence reviewed: `app.py`, `README.md`, `半自动推广工具_数据源与指标说明.md`, current MetoX v2.2 screenshot supplied by the user.

## Brand
- Personality: operational, direct, trustworthy, low-friction.
- Trust signals: explicit data acceptance, metric lineage, risk gates, visible calculation reasons.
- Avoid: configuration-heavy first screens, duplicated controls, and labels that imply automation beyond the evidence.

## Product goals
- Goals: let an operator load the normal daily dataset with minimal scanning; keep low-frequency configuration discoverable but unobtrusive; keep product identity immediately recognizable in action tables; surface a concise, reconciled operating explanation immediately below the dashboard KPIs.
- Non-goals: redesigning the analysis tabs, changing formulas, or hiding data-quality evidence.
- Success signals: the collapsed sidebar presents date, period, and load action first, followed by one compact advanced entry; loading and threshold reapplication behave unchanged.

## Personas and jobs
- Primary personas: Tmall operators doing daily promotion review; maintainers configuring paths or thresholds occasionally.
- User jobs: load a date, choose a time window, validate data, review risk and actions, export execution records.
- Key contexts of use: desktop browser, repeated daily workflow, shared-drive data source.

## Information architecture
- Primary navigation: eight existing analysis tabs remain unchanged.
- Core routes/screens: single Streamlit page with sidebar loading controls and tabbed analysis content.
- Content hierarchy: daily inputs first; all paths, data-source directories, thresholds, diagnostics, and version controls under one `高级设置` disclosure. On the dashboard, six KPIs lead, followed by `AI经营日报`, then grade cards and the existing diagnostic sections.

## Design principles
- Principle 1: frequent actions stay visible; low-frequency settings use progressive disclosure.
- Principle 2: data-quality evidence remains available without consuming the report first screen.
- Tradeoffs: one advanced panel is longer when opened, but the default daily workflow is materially shorter.

## Visual language
- Color: retain Streamlit theme and existing primary-action color.
- Typography: retain existing Streamlit hierarchy.
- Spacing/layout rhythm: date, period, and primary load button form one uninterrupted daily flow; the compact advanced disclosure follows them.
- Shape/radius/elevation: retain native Streamlit components.
- Motion: native disclosure only.
- Imagery/iconography: no new imagery; use restrained text labels.

## Components
- Existing components to reuse: `st.sidebar`, `st.expander`, `st.text_input`, `st.selectbox`, `st.button`, `st.caption`, `st.dataframe`.
- New/changed components: a single collapsed `高级设置`; threshold reapply button moves inside its threshold section; inventory action tables use the stable identity order `宝贝ID` → `货号` → `商品名称` on page and in Excel; `AI经营日报` is a bordered, default-expanded five-line summary generated from deterministic local metrics.
- Variants and states: before load, threshold inputs are editable but reapply is absent; after load, references, reapply, performance details, and period completeness are available inside advanced settings. A complete comparison window shows overview, grade, category, channel, and risk lines; an incomplete window shows a completeness warning and provisional overview only.
- Token/component ownership: Streamlit native theme; no new CSS or component library.

## Accessibility
- Target standard: preserve native Streamlit labels, focus order, keyboard activation, and contrast.
- Keyboard/focus behavior: frequent controls precede the collapsed advanced disclosure and load action in DOM order.
- Contrast/readability: retain native theme.
- Screen-reader semantics: use labeled native controls and disclosure heading.
- Reduced motion and sensory considerations: no custom animation.

## Responsive behavior
- Supported breakpoints/devices: existing desktop-first Streamlit behavior.
- Layout adaptations: sidebar remains single column; advanced content follows the same width.
- Touch/hover differences: native Streamlit behavior.

## Interaction states
- Loading: existing spinner and success/error messages remain unchanged.
- Empty: existing instruction to enter a date remains unchanged.
- Error: existing date/path validation remains unchanged; incomplete comparison windows suppress driver/risk conclusions instead of presenting partial data as final.
- Success: existing loaded session state and rerun remain unchanged; the report reconciles current and previous product universes before ranking drivers.
- Disabled: no new disabled state.
- Offline/slow network, if applicable: local/offline application behavior remains unchanged.

## Content voice
- Tone: concise operational Chinese.
- Terminology: `日期`, `时间周期`, `加载并处理数据`, `高级设置`, `自定义分界点`, `AI经营日报`, `归因成交`, `流失商品`.
- Microcopy rules: advanced section headings describe the configuration class; defaults and help text retain existing detail. The report says growth is concentrated in a grade/category, never that the grade/category caused growth; channel movement is explicitly labeled advertising-attributed sales rather than total-store GMV contribution.

## Implementation constraints
- Framework/styling system: Streamlit/Python; preserve session-state keys and computation functions.
- Design-token constraints: no custom CSS.
- Performance constraints: collapsing controls must not trigger new data reads or calculations. The operating report must reuse in-memory current/previous frames, perform no file IO, and call no external AI service.
- Compatibility constraints: packaged PyInstaller/Streamlit runtime and three launcher entries remain unchanged.
- Test/screenshot expectations: AST/static tests for hierarchy and button placement, unit suite, release check, and Streamlit AppTest smoke when available.

## Open questions
- [ ] Whether performance timing should later move from advanced settings to a dedicated diagnostics tab; current change keeps it inside advanced to satisfy the compact-first-screen contract.
