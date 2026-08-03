# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-08-03
- Primary product surfaces: Streamlit sidebar data-loading flow, main analysis tabs, AI任务视图, Excel export.
- Evidence reviewed: `app.py`, `core/task_engine.py`, `test_ai_task_view.py`, `README.md`, `半自动推广工具_数据源与指标说明.md`, current MetoX v2.2 screenshot supplied by the user, AI任务视图V2.7字段与可信度要求。

## Brand
- Personality: operational, direct, trustworthy, low-friction.
- Trust signals: explicit data acceptance, metric lineage, risk gates, visible calculation reasons.
- Avoid: configuration-heavy first screens, duplicated controls, and labels that imply automation beyond the evidence.

## Product goals
- Goals: let an operator load the normal daily dataset with minimal scanning; keep low-frequency configuration discoverable but unobtrusive; keep product identity immediately recognizable in action tables; surface a concise, reconciled operating explanation immediately below the dashboard KPIs; make the AI任务主表 answer priority, product, operating performance, strategy, action, validation, and confidence without exposing diagnostic sub-scores.
- Non-goals: redesigning the analysis tabs, changing formulas, changing MetoX or any product/channel/product-plan decision rule, or hiding data-quality evidence.
- Success signals: the collapsed sidebar presents date, period, and load action first, followed by one compact advanced entry; loading and threshold reapplication behave unchanged. AI任务在无计划时明确显示“无计划”，不把不存在的计划解释成100%覆盖或满分可信度。

## Personas and jobs
- Primary personas: Tmall operators doing daily promotion review; maintainers configuring paths or thresholds occasionally.
- User jobs: load a date, choose a time window, validate data, review risk and actions, export execution records.
- Key contexts of use: desktop browser, repeated daily workflow, shared-drive data source.

## Information architecture
- Primary navigation: eight existing analysis tabs remain unchanged.
- Core routes/screens: single Streamlit page with sidebar loading controls and tabbed analysis content.
- Content hierarchy: daily inputs first; all paths, data-source directories, thresholds, diagnostics, and version controls under one `高级设置` disclosure. On the dashboard, six KPIs lead, followed by `AI经营日报`, then grade cards and the existing diagnostic sections. In `AI任务视图`, the main table follows `优先级 → 商品身份 → 经营指标 → 商品/渠道/计划动作 → 验证条件 → 综合AI可信度 → 库存提醒`.

## Design principles
- Principle 1: frequent actions stay visible; low-frequency settings use progressive disclosure.
- Principle 2: data-quality evidence remains available without consuming the report first screen.
- Principle 3: one operational concept has one visible score; confidence diagnostics remain available on demand rather than becoming extra main-table columns.
- Tradeoffs: one advanced panel is longer when opened, but the default daily workflow is materially shorter. The AI任务主表及其导出不包含`商品名称`，以减少横向扫描；商品通过宝贝ID、货号和款式主图识别。可信度详情保留支持性上下文。

## Visual language
- Color: retain Streamlit theme and existing primary-action color.
- Typography: retain existing Streamlit hierarchy.
- Spacing/layout rhythm: date, period, and primary load button form one uninterrupted daily flow; the compact advanced disclosure follows them.
- Shape/radius/elevation: retain native Streamlit components.
- Motion: native disclosure only.
- Imagery/iconography: no new imagery; use restrained text labels.

## Components
- Existing components to reuse: `st.sidebar`, `st.expander`, `st.text_input`, `st.selectbox`, `st.button`, `st.caption`, `st.dataframe`.
- New/changed components: a single collapsed `高级设置`; threshold reapply button moves inside its threshold section; inventory action tables use the stable identity order `宝贝ID` → `货号` → `商品名称` on page and in Excel; `AI经营日报` is a bordered, default-expanded five-line summary generated from deterministic local metrics; the AI任务主表 is an explicit presentation projection rather than every backend column; a selected task exposes one `AI可信度说明` disclosure.
- Variants and states: before load, threshold inputs are editable but reapply is absent; after load, references, reapply, performance details, and period completeness are available inside advanced settings. A complete comparison window shows overview, grade, category, channel, and risk lines; an incomplete window shows a completeness warning and provisional overview only. AI可信度 always renders as one five-star-plus-percentage value; the detail uses `✓` for complete evidence and `△` for partial/missing evidence without exposing product/channel/plan sub-scores. `计划总数=0`时，计划覆盖率显示`无计划`；AI可信度仅按商品和渠道证据计算并设为非五星上限，详情明确写明`当前无计划数据，计划可信度未参与计算`。
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
- Detail disclosure: selecting a task reveals the row-specific confidence checklist and plan detail; opening it does not recalculate data or alter the task.
- Disabled: no new disabled state.
- Offline/slow network, if applicable: local/offline application behavior remains unchanged.

## Content voice
- Tone: concise operational Chinese.
- Terminology: `日期`, `时间周期`, `加载并处理数据`, `高级设置`, `自定义分界点`, `AI经营日报`, `AI可信度`, `归因成交`, `流失商品`.
- Microcopy rules: advanced section headings describe the configuration class; defaults and help text retain existing detail. The report says growth is concentrated in a grade/category, never that the grade/category caused growth; channel movement is explicitly labeled advertising-attributed sales rather than total-store GMV contribution. The main table says `商品级策略` even when the backend field remains `商品级总策略`; confidence appears as `★★★★☆（88%）`, never as separate product/channel/plan percentages.

## Implementation constraints
- Framework/styling system: Streamlit/Python; preserve session-state keys and computation functions.
- Design-token constraints: no custom CSS.
- Performance constraints: collapsing controls must not trigger new data reads or calculations. The operating report must reuse in-memory current/previous frames, perform no file IO, and call no external AI service. AI任务中的`GMV`、`商品访客数`、`链接投产`必须从`st.session_state.df`对应同名字段原值透传，不得重算。
- Compatibility constraints: packaged PyInstaller/Streamlit runtime and three launcher entries remain unchanged.
- Test/screenshot expectations: AST/static tests for hierarchy, exact AI任务主表投影和隐藏字段；透传字段回归；可信度不改变策略回归；单元测试、发布检查和Streamlit AppTest smoke when available.

## Open questions
- [ ] Whether performance timing should later move from advanced settings to a dedicated diagnostics tab; current change keeps it inside advanced to satisfy the compact-first-screen contract.
