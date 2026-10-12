// 計測結果の一覧と、選択したrunのPython/Rust比較を表示する。
// 値はすべてサーバーのbuild_report()が計算したものを使い、画面側では再計算しない。

const IMPLEMENTATIONS = [
  { key: "python", label: "Python" },
  { key: "rust", label: "Rust" },
];

// 指標ごとの表示名・説明・単位と、Rustが小さいときの言い方。いずれも小さいほど有利な指標。
const METRICS = {
  latency_ms: {
    label: "応答時間",
    description: "クライアントから見たAPI全体の応答時間です。",
    unit: "ms",
    lower: "短い",
    higher: "長い",
  },
  duration_ms: {
    label: "Lambda実行時間",
    description: "Lambda内でハンドラーを実行した時間です。",
    unit: "ms",
    lower: "短い",
    higher: "長い",
  },
  billed_duration_ms: {
    label: "課金対象時間",
    description: "Lambda料金の計算に使われる時間です。",
    unit: "ms",
    lower: "短い",
    higher: "長い",
  },
  init_duration_ms: {
    label: "初期化時間",
    description: "コールドスタート時の初期化時間です。コールドの計測だけに値があります。",
    unit: "ms",
    lower: "短い",
    higher: "長い",
  },
  max_memory_used_mb: {
    label: "最大メモリ使用量",
    description: "実行中に観測された最大メモリ使用量です。",
    unit: "MB",
    lower: "少ない",
    higher: "多い",
  },
};

const SCOPES = [
  { key: "metrics", label: "全体" },
  { key: "cold_metrics", label: "コールドスタート" },
  { key: "warm_metrics", label: "ウォームスタート" },
];

const PERCENTILES = [
  { key: "p50", label: "P50", description: "中央値" },
  { key: "p95", label: "P95", description: "遅い方から5%" },
  { key: "p99", label: "P99", description: "遅い方から1%" },
];

// 差がこの割合未満なら、計測の揺らぎの範囲として「ほぼ同じ」と表示する。
const SAME_THRESHOLD_PERCENT = 1;

const state = {
  runs: [],
  selectedId: null,
  run: null,
  metric: "latency_ms",
  scope: "metrics",
};

const runListBody = document.getElementById("run-list-body");
const detail = document.getElementById("run-detail");
const tooltip = document.getElementById("tooltip");

// --- DOM補助 ---------------------------------------------------------------

// 計測結果に由来する文字列を安全に扱うため、要素はtextContentだけで組み立てる。
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === "text") node.textContent = value;
    else if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  for (const child of [].concat(children)) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name === "text") node.textContent = value;
    else node.setAttribute(name, value);
  }
  for (const child of [].concat(children)) {
    if (child) node.append(child);
  }
  return node;
}

// --- 表示形式 ----------------------------------------------------------------

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// 有効数字を保ちつつ、大きい値は桁区切り、小さい値は小数で表示する。
function formatNumber(value) {
  if (!isNumber(value)) return "未取得";
  const abs = Math.abs(value);
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function formatValue(value, unit) {
  return isNumber(value) ? `${formatNumber(value)} ${unit}` : "未取得";
}

function formatCost(value) {
  if (!isNumber(value)) return "未計算";
  return `$${value.toLocaleString("en-US", { maximumSignificantDigits: 4 })}`;
}

function formatDate(value) {
  if (!value) return "日時不明";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatOptional(value, suffix = "") {
  return value === null || value === undefined || value === "" ? "未記録" : `${value}${suffix}`;
}

// Pythonを基準にしたRustの増減率を、比較の向きが分かる文にする。
function describeDifference(difference, lowerWord, higherWord) {
  if (!isNumber(difference)) return "比較できません（どちらかの値がありません）";
  if (Math.abs(difference) < SAME_THRESHOLD_PERCENT) return "Rust と Python はほぼ同じ";
  const amount = Math.abs(difference).toFixed(1);
  const word = difference < 0 ? lowerWord : higherWord;
  return `Rust は Python より ${amount}% ${word}`;
}

function relativeDifference(reference, comparison) {
  if (!isNumber(reference) || !isNumber(comparison) || reference === 0) return null;
  return ((comparison - reference) / reference) * 100;
}

// --- 通信 ------------------------------------------------------------------

async function fetchJson(path) {
  let response;
  try {
    response = await fetch(path, { cache: "no-store" });
  } catch {
    throw new Error("画面のサーバーに接続できません。ターミナルで make ui が動いているか確認してください。");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `読み込みに失敗しました（HTTP ${response.status}）`);
  return payload;
}

// --- 計測履歴 ----------------------------------------------------------------

function statusBadge(comparable) {
  return comparable
    ? el("span", { class: "badge badge-good", text: "比較可能" })
    : el("span", { class: "badge badge-warning", text: "要確認" });
}

function renderRunList() {
  runListBody.replaceChildren();
  if (state.runs.length === 0) {
    runListBody.append(
      el("p", { class: "muted", text: "計測結果はまだありません。" }),
      el("p", { class: "muted" }, [
        "make benchmark で計測すると、ここに表示されます。",
      ]),
    );
    return;
  }
  const list = el("ul");
  for (const run of state.runs) {
    const conditions = [
      run.architecture ?? "アーキテクチャ未記録",
      run.memory_mb ? `${run.memory_mb} MB` : "メモリ未記録",
      run.iterations ? `${run.iterations} 回` : null,
    ].filter(Boolean);
    const button = el(
      "button",
      {
        type: "button",
        class: "run-item",
        "aria-current": String(run.run_id === state.selectedId),
        onclick: () => selectRun(run.run_id),
      },
      [
        el("span", { class: "run-item-title" }, [
          el("span", { text: run.workload ?? run.run_id }),
          statusBadge(run.comparable),
        ]),
        el("span", { class: "run-item-meta", text: formatDate(run.started_at) }),
        el("br"),
        el("span", { class: "run-item-meta", text: conditions.join(" · ") }),
      ],
    );
    list.append(el("li", {}, button));
  }
  runListBody.append(list);
}

// --- 詳細 --------------------------------------------------------------------

function renderNotice(run) {
  if (run.comparable) {
    return el("div", { class: "card notice notice-good" }, [
      el("h3", { class: "badge badge-good", text: "この計測は比較に使えます" }),
      el("p", {
        class: "section-note",
        text: "実験条件がそろい、すべての計測が Lambda のログと結び付いています。",
      }),
    ]);
  }
  return el("div", { class: "card notice" }, [
    el("h3", { class: "badge badge-warning", text: "この計測には確認が必要な点があります" }),
    el("ul", {}, run.issues.map((issue) => el("li", { text: issue }))),
  ]);
}

function renderTile({ label, values, format, difference }) {
  const rows = IMPLEMENTATIONS.map(({ key, label: name }) =>
    el("div", { class: "tile-row" }, [
      el("span", { class: `series-key series-${key}`, text: name }),
      el("span", { class: "tile-value" }, format(values[key])),
    ]),
  );
  return el("div", { class: "card" }, [
    el("p", { class: "tile-label", text: label }),
    ...rows,
    el("p", { class: "tile-delta", text: difference }),
  ]);
}

function valueWithUnit(value, unit) {
  if (!isNumber(value)) return [el("span", { class: "tile-unit", text: "未取得" })];
  return [formatNumber(value), el("span", { class: "tile-unit", text: unit })];
}

function renderTiles(run) {
  const implementations = run.implementations;
  const metricValue = (key, metric, percentile) =>
    implementations[key]?.metrics?.[metric]?.[percentile] ?? null;
  const tiles = [];

  for (const percentile of ["p50", "p95"]) {
    const values = Object.fromEntries(
      IMPLEMENTATIONS.map(({ key }) => [key, metricValue(key, "latency_ms", percentile)]),
    );
    tiles.push(
      renderTile({
        label: `応答時間 ${percentile.toUpperCase()}（${percentile === "p50" ? "中央値" : "遅い方から5%"}）`,
        values,
        format: (value) => valueWithUnit(value, "ms"),
        difference: describeDifference(run.differences?.latency_ms?.[percentile], "短い", "長い"),
      }),
    );
  }

  const costs = Object.fromEntries(
    IMPLEMENTATIONS.map(({ key }) => [key, implementations[key]?.cost?.estimated_cost_per_1m ?? null]),
  );
  tiles.push(
    renderTile({
      label: "100万リクエストあたりの推定 Lambda 料金",
      values: costs,
      format: (value) =>
        isNumber(value) ? formatCost(value) : [el("span", { class: "tile-unit", text: "未計算" })],
      difference: describeDifference(relativeDifference(costs.python, costs.rust), "安い", "高い"),
    }),
  );
  return el("div", { class: "tiles" }, tiles);
}

function segmented(options, selected, onSelect, label) {
  return el(
    "div",
    { class: "segmented", role: "group", "aria-label": label },
    options.map((option) =>
      el("button", {
        type: "button",
        "aria-pressed": String(option.key === selected),
        text: option.label,
        onclick: () => onSelect(option.key),
      }),
    ),
  );
}

function legend() {
  return el(
    "div",
    { class: "legend", "aria-label": "凡例" },
    IMPLEMENTATIONS.map(({ key, label }) => el("span", { class: `series-key series-${key}`, text: label })),
  );
}

// 目盛りを1・2・5系の切りのよい間隔にそろえる。
function niceStep(maxValue, targetTicks) {
  const rough = maxValue / targetTicks;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const residual = rough / magnitude;
  const factor = residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1;
  return factor * magnitude;
}

// 右端だけ4pxの角丸、基線側は四角い横棒のパスを返す。
function barPath(x0, x1, y, height) {
  const radius = Math.min(4, (x1 - x0) / 2, height / 2);
  return [
    `M${x0},${y}`,
    `H${x1 - radius}`,
    `Q${x1},${y} ${x1},${y + radius}`,
    `V${y + height - radius}`,
    `Q${x1},${y + height} ${x1 - radius},${y + height}`,
    `H${x0}`,
    "Z",
  ].join(" ");
}

function showTooltip(event, value, caption) {
  tooltip.replaceChildren(el("strong", { text: value }), el("span", { text: caption }));
  tooltip.hidden = false;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX ?? rect.right;
  const y = event.clientY ?? rect.top;
  const width = tooltip.offsetWidth;
  tooltip.style.left = `${Math.min(x + 12, window.innerWidth - width - 8)}px`;
  tooltip.style.top = `${y + 12}px`;
}

function hideTooltip() {
  tooltip.hidden = true;
}

function renderChart(container, run) {
  const metric = METRICS[state.metric];
  const scopeLabel = SCOPES.find((scope) => scope.key === state.scope).label;
  const statsFor = (key) => run.implementations[key]?.[state.scope]?.[state.metric];
  const values = PERCENTILES.flatMap(({ key: percentile }) =>
    IMPLEMENTATIONS.map(({ key }) => statsFor(key)?.[percentile]),
  ).filter(isNumber);

  const width = Math.max(container.clientWidth, 280);
  const margin = { top: 8, right: 72, bottom: 28, left: 44 };
  const barHeight = 18;
  const barGap = 2;
  const groupGap = 20;
  const groupHeight = IMPLEMENTATIONS.length * barHeight + (IMPLEMENTATIONS.length - 1) * barGap;
  const plotHeight = PERCENTILES.length * groupHeight + (PERCENTILES.length - 1) * groupGap;
  const height = margin.top + plotHeight + margin.bottom;
  const plotWidth = width - margin.left - margin.right;

  const maxValue = values.length ? Math.max(...values) : 1;
  const step = niceStep(maxValue || 1, 4);
  const domainMax = Math.ceil((maxValue || 1) / step) * step;
  const scale = (value) => margin.left + (value / domainMax) * plotWidth;

  const root = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    height,
    role: "img",
    "aria-label": `${metric.label}（${scopeLabel}）の百分位ごとの比較`,
  });

  // 目盛り線と軸ラベル。データより目立たない細線にする。
  for (let tick = 0; tick <= domainMax + step / 2; tick += step) {
    const x = scale(tick);
    root.append(
      svg("line", {
        class: tick === 0 ? "baseline" : "grid",
        x1: x,
        x2: x,
        y1: margin.top,
        y2: margin.top + plotHeight,
      }),
      svg("text", {
        class: "axis-label",
        x,
        y: height - 8,
        "text-anchor": "middle",
        text: formatNumber(tick),
      }),
    );
  }
  root.append(
    svg("text", {
      class: "axis-label",
      x: width - 4,
      y: height - 8,
      "text-anchor": "end",
      text: metric.unit,
    }),
  );

  PERCENTILES.forEach((percentile, groupIndex) => {
    const groupTop = margin.top + groupIndex * (groupHeight + groupGap);
    root.append(
      svg("text", {
        class: "row-label",
        x: 0,
        y: groupTop + groupHeight / 2 + 4,
        text: percentile.label,
      }),
    );
    IMPLEMENTATIONS.forEach(({ key, label }, index) => {
      const y = groupTop + index * (barHeight + barGap);
      const value = statsFor(key)?.[percentile.key];
      const caption = `${label} · ${percentile.label}（${percentile.description}）· ${scopeLabel}`;
      const group = svg("g", {
        class: "bar-hit",
        tabindex: "0",
        "aria-label": `${caption}: ${formatValue(value, metric.unit)}`,
      });
      // ホバー判定を棒より広くとるため、行全体に透明な当たり領域を置く。
      group.append(
        svg("rect", {
          x: margin.left,
          y: y - barGap / 2,
          width: plotWidth,
          height: barHeight + barGap,
          fill: "transparent",
        }),
      );
      if (isNumber(value)) {
        const x1 = Math.max(scale(value), margin.left + 1);
        group.append(
          svg("path", { d: barPath(margin.left, x1, y, barHeight), fill: `var(--${key})` }),
          svg("text", { x: x1 + 6, y: y + barHeight / 2 + 4, text: formatNumber(value) }),
        );
      } else {
        group.append(svg("text", { x: margin.left + 6, y: y + barHeight / 2 + 4, text: "未取得" }));
      }
      const text = formatValue(value, metric.unit);
      group.addEventListener("pointermove", (event) => showTooltip(event, text, caption));
      group.addEventListener("pointerleave", hideTooltip);
      group.addEventListener("focus", (event) => showTooltip(event, text, caption));
      group.addEventListener("blur", hideTooltip);
      root.append(group);
    });
  });

  container.replaceChildren(root);
}

function renderComparison(run) {
  const metric = METRICS[state.metric];
  const chart = el("div", { class: "chart" });
  const counts = IMPLEMENTATIONS.map(({ key, label }) => {
    const count = run.implementations[key]?.[state.scope]?.[state.metric]?.count ?? 0;
    return `${label} ${count.toLocaleString("ja-JP")} 件`;
  }).join(" / ");

  const card = el("div", { class: "card", id: "comparison" }, [
    el("div", { class: "section-head" }, [el("h3", { text: "百分位ごとの比較" }), legend()]),
    el("p", {
      class: "section-note",
      text: `${metric.description} 値が小さいほど有利です。`,
    }),
    el("div", { class: "controls" }, [
      segmented(
        Object.entries(METRICS).map(([key, value]) => ({ key, label: value.label })),
        state.metric,
        (key) => {
          state.metric = key;
          refreshComparison("指標");
        },
        "指標",
      ),
      segmented(
        SCOPES,
        state.scope,
        (key) => {
          state.scope = key;
          refreshComparison("対象の計測");
        },
        "対象の計測",
      ),
    ]),
    chart,
    el("p", { class: "chart-count", text: `集計した計測の件数: ${counts}` }),
  ]);
  // 描画幅を確定させてからSVGを組み立てる。
  requestAnimationFrame(() => renderChart(chart, run));
  return card;
}

function renderPercentileTable(run) {
  const scopeLabel = SCOPES.find((scope) => scope.key === state.scope).label;
  const header = el("tr", {}, [
    el("th", { text: "指標" }),
    el("th", { class: "text", text: "実装" }),
    el("th", { text: "件数" }),
    ...PERCENTILES.map(({ label }) => el("th", { text: label })),
  ]);
  const rows = [];
  for (const [metricKey, metric] of Object.entries(METRICS)) {
    for (const { key, label } of IMPLEMENTATIONS) {
      const stats = run.implementations[key]?.[state.scope]?.[metricKey];
      rows.push(
        el("tr", {}, [
          el("td", { text: `${metric.label}（${metric.unit}）` }),
          el("td", { class: "text", text: label }),
          el("td", { text: (stats?.count ?? 0).toLocaleString("ja-JP") }),
          ...PERCENTILES.map(({ key: percentile }) => el("td", { text: formatNumber(stats?.[percentile]) })),
        ]),
      );
    }
  }
  return el("details", { class: "card", id: "percentile-table" }, [
    el("summary", { text: `すべての指標を数値表で見る（${scopeLabel}）` }),
    el("div", { class: "table-scroll" }, el("table", {}, [el("thead", {}, header), el("tbody", {}, rows)])),
  ]);
}

function renderSampleTable(run) {
  const header = el("tr", {}, [
    el("th", { class: "text", text: "実装" }),
    el("th", { text: "計測件数" }),
    el("th", { text: "コールド" }),
    el("th", { text: "ウォーム" }),
    el("th", { text: "ログと未結合" }),
    el("th", { text: "エラー率" }),
  ]);
  const rows = IMPLEMENTATIONS.filter(({ key }) => run.implementations[key]).map(({ key, label }) => {
    const values = run.implementations[key];
    const errorRate = isNumber(values.error_rate) ? `${(values.error_rate * 100).toFixed(2)}%` : "未取得";
    return el("tr", {}, [
      el("td", { class: "text", text: label }),
      el("td", { text: values.sample_count.toLocaleString("ja-JP") }),
      el("td", { text: values.cold_count.toLocaleString("ja-JP") }),
      el("td", { text: values.warm_count.toLocaleString("ja-JP") }),
      el("td", { text: values.unclassified_count.toLocaleString("ja-JP") }),
      el("td", { text: errorRate }),
    ]);
  });
  return el("div", { class: "card" }, [
    el("div", { class: "section-head" }, el("h3", { text: "計測件数" })),
    el("p", {
      class: "section-note",
      text: "コールドは初期化を伴った呼び出しです。ログと未結合の計測は、Lambda 内部の指標を持ちません。",
    }),
    el("div", { class: "table-scroll" }, el("table", {}, [el("thead", {}, header), el("tbody", {}, rows)])),
  ]);
}

function renderConditions(run) {
  const metadata = run.metadata;
  const workload = metadata.workload ?? {};
  const pricing = run.pricing;
  const entries = [
    ["開始", formatDate(metadata.started_at)],
    ["終了", formatDate(metadata.completed_at)],
    ["ワークロード", formatOptional(workload.name)],
    ["リクエスト", workload.method && workload.path ? `${workload.method} ${workload.path}` : "未記録"],
    ["反復回数（各実装）", formatOptional(workload.iterations, " 回")],
    ["同時実行数", formatOptional(workload.client_concurrency)],
    ["ウォームアップ（各実装）", formatOptional(workload.warmup, " 回")],
    ["アーキテクチャ", formatOptional(metadata.architecture)],
    ["Lambda メモリ", formatOptional(metadata.memory_mb, " MB")],
    ["リージョン", formatOptional(metadata.region)],
    ["Git リビジョン", metadata.git_revision ? metadata.git_revision.slice(0, 12) : "未記録"],
    [
      "料金表",
      pricing
        ? `${pricing.region ?? ""} ${pricing.updated_at ?? ""} 時点`.trim()
        : `未設定（${run.pricing_note ?? "理由不明"}）`,
    ],
  ];
  return el("div", { class: "card" }, [
    el("div", { class: "section-head" }, el("h3", { text: "実験条件" })),
    el("p", {
      class: "section-note",
      text: "条件が異なる計測同士の数値は、そのまま比べられません。料金は Free Tier・API Gateway・DynamoDB を含まない概算です。",
    }),
    el(
      "dl",
      { class: "conditions" },
      entries.flatMap(([term, value]) => [el("dt", { text: term }), el("dd", { text: value })]),
    ),
  ]);
}

function renderImageMetrics(run) {
  if (!run.image_metrics) return null;
  return el("details", { class: "card" }, [
    el("summary", { text: "コンテナイメージの情報" }),
    el("pre", { text: JSON.stringify(run.image_metrics, null, 2) }),
  ]);
}

// 指標・対象の切り替えでは比較グラフと数値表だけを差し替え、表の開閉と操作中のボタンを保つ。
function refreshComparison(groupLabel) {
  hideTooltip();
  const table = document.getElementById("percentile-table");
  const wasOpen = table?.open ?? false;
  document.getElementById("comparison").replaceWith(renderComparison(state.run));
  const newTable = renderPercentileTable(state.run);
  newTable.open = wasOpen;
  table.replaceWith(newTable);
  document
    .querySelector(`#comparison [aria-label="${groupLabel}"] [aria-pressed="true"]`)
    ?.focus();
}

function renderDetail() {
  hideTooltip();
  const run = state.run;
  if (!run) return;
  const workload = run.metadata.workload?.name ?? "ワークロード不明";
  detail.replaceChildren(
    el("div", { class: "detail-head" }, [
      el("h2", { text: `${workload} の計測結果` }),
      el("p", { text: `${formatDate(run.metadata.started_at)} 開始 · Run ID ${run.run_id}` }),
    ]),
    renderNotice(run),
    renderTiles(run),
    renderComparison(run),
    renderPercentileTable(run),
    renderSampleTable(run),
    renderConditions(run),
    // イメージ情報は未収集のrunもあるため、存在する場合だけ表示する。
    ...[renderImageMetrics(run)].filter(Boolean),
  );
}

function renderError(target, message, retry) {
  target.replaceChildren(
    el("div", { class: "card notice notice-error" }, [
      el("h3", { text: "読み込めませんでした" }),
      el("p", { class: "section-note", text: message }),
      el("button", { type: "button", class: "ghost-button", text: "もう一度読み込む", onclick: retry }),
    ]),
  );
}

// --- 状態遷移 ----------------------------------------------------------------

async function selectRun(runId) {
  state.selectedId = runId;
  history.replaceState(null, "", `#run=${encodeURIComponent(runId)}`);
  renderRunList();
  detail.replaceChildren(el("p", { class: "muted", text: "読み込み中…" }));
  try {
    const run = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
    // 読み込み中に別のrunが選ばれていたら、古い応答で表示を上書きしない。
    if (state.selectedId !== runId) return;
    state.run = run;
    renderDetail();
  } catch (error) {
    renderError(detail, error.message, () => selectRun(runId));
  }
}

function runIdFromHash() {
  const match = location.hash.match(/^#run=(.+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

async function loadRuns() {
  runListBody.replaceChildren(el("p", { class: "muted", text: "読み込み中…" }));
  try {
    const { runs } = await fetchJson("/api/runs");
    state.runs = runs;
  } catch (error) {
    renderError(runListBody, error.message, loadRuns);
    detail.replaceChildren();
    return;
  }
  renderRunList();
  if (state.runs.length === 0) {
    detail.replaceChildren(
      el("div", { class: "card" }, [
        el("h3", { text: "表示できる計測結果がありません" }),
        el("p", {
          class: "section-note",
          text: "計測すると results/raw に結果が保存され、ここで比較できるようになります。",
        }),
      ]),
    );
    return;
  }
  // URLで指定されたrunがあればそれを、なければ最新のrunを開く。
  const requested = runIdFromHash();
  const initial = state.runs.some((run) => run.run_id === requested) ? requested : state.runs[0].run_id;
  await selectRun(initial);
}

document.getElementById("reload").addEventListener("click", loadRuns);

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderDetail, 150);
});

loadRuns();
