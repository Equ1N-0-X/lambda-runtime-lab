// 計測結果の一覧と、選択したrunの言語間の比較を表示する。
// 値はすべてサーバーのbuild_report()が計算したものを使い、画面側では再計算しない。

// 計測した言語を、サーバーが返した表示順（実装一覧の順）で返す。色は表示順で割り当てる。
function implementationsOf(run) {
  return Object.keys(run.implementations).map((key, index) => ({
    key,
    label: run.labels?.[key] ?? key,
    series: `series-${(index % 6) + 1}`,
  }));
}

// 指標ごとの表示名・何を表すか・何が分かるか・単位と、Rustが小さいときの言い方。
// いずれも小さいほど有利な指標。
const METRICS = {
  latency_ms: {
    label: "応答時間",
    description: "リクエストを送ってから応答が届くまでの時間です。手元の回線の速さも含みます。",
    insight: "利用者が待つ時間の差が分かります。",
    unit: "ms",
    lower: "短い",
    higher: "長い",
  },
  duration_ms: {
    label: "Lambda実行時間",
    description: "Lambda がリクエストを処理していた時間です。回線の影響を含みません。",
    insight: "言語と実装そのものの処理の速さが分かります。",
    unit: "ms",
    lower: "短い",
    higher: "長い",
  },
  billed_duration_ms: {
    label: "課金対象時間",
    description: "Lambda 料金の計算に使われる時間です（1ms 単位で切り上げ）。",
    insight: "実行時間分の料金はこの時間に比例します。",
    unit: "ms",
    lower: "短い",
    higher: "長い",
  },
  init_duration_ms: {
    label: "初期化時間",
    description: "新しい実行環境を起動するときの準備時間です。コールドスタートの計測だけに値があります。",
    insight: "しばらく呼ばれなかった後や、アクセスが急に増えたときの最初の応答の遅さが分かります。",
    unit: "ms",
    lower: "短い",
    higher: "長い",
  },
  max_memory_used_mb: {
    label: "最大メモリ使用量",
    description: "1回の実行で使ったメモリの最大値です。",
    insight: "Lambda のメモリ設定をどこまで下げられるかの目安になります。",
    unit: "MB",
    lower: "少ない",
    higher: "多い",
  },
};

const SCOPES = [
  { key: "metrics", label: "全体", description: "すべての計測を対象にしています。" },
  {
    key: "cold_metrics",
    label: "コールドスタート",
    description: "実行環境の起動を伴った呼び出しだけを対象にしています。",
  },
  {
    key: "warm_metrics",
    label: "ウォームスタート",
    description: "起動済みの実行環境で処理した呼び出しだけを対象にしています。普段の性能はこちらで比べます。",
  },
];

const PERCENTILES = [
  { key: "p50", label: "P50", description: "中央値" },
  { key: "p95", label: "P95", description: "95%がこの値以下" },
  { key: "p99", label: "P99", description: "99%がこの値以下" },
];

const state = {
  runs: [],
  selectedId: null,
  run: null,
  metric: "latency_ms",
  scope: "metrics",
  // "run" は計測結果、"launch" は計測開始の表示。
  view: "run",
  launch: null,
  job: null,
  // この画面を開いている間に実行中の状態を確認した計測のrun ID。
  // 終わった計測の結果を出すのは、利用者が見届けた計測だけに限る。
  watchedRunId: null,
};

// 実行中の計測の状態を問い合わせる間隔。
const JOB_POLL_MS = 2000;

const runListBody = document.getElementById("run-list-body");
const detail = document.getElementById("run-detail");
const tooltip = document.getElementById("tooltip");
const jobBanner = document.getElementById("job-banner");

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

// --- 通信 ------------------------------------------------------------------

async function fetchJson(path, body) {
  let response;
  // 本文があればJSONのPOST、なければGETとして送る。サーバーはJSON以外のPOSTを拒否する。
  const options = body === undefined
    ? { cache: "no-store" }
    : { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("画面のサーバーに接続できません。ターミナルで make ui が動いているか確認してください。");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `読み込みに失敗しました（HTTP ${response.status}）`);
  return payload;
}

// --- 計測履歴 ----------------------------------------------------------------

// 比較に使えない計測だけに印を付け、通常の計測は印なしにして注意を目立たせる。
function statusBadge(comparable) {
  return comparable ? null : el("span", { class: "badge badge-warning", text: "要確認" });
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
      run.iterations ? `各 ${run.iterations} 回` : null,
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
        el("span", { class: "run-item-languages", text: (run.languages ?? []).join(" · ") }),
        el("span", { class: "run-item-meta", text: conditions.join(" · ") }),
      ],
    );
    list.append(el("li", {}, button));
  }
  runListBody.append(list);
}

// --- 詳細 --------------------------------------------------------------------

// 比較に使えない理由があるときだけ注意を出す。問題がない計測では何も出さない。
function renderNotice(run) {
  if (run.comparable) return null;
  return el("div", { class: "card notice" }, [
    el("h3", { class: "badge badge-warning", text: "この計測には確認が必要な点があります" }),
    el("ul", {}, run.issues.map((issue) => el("li", { text: issue }))),
  ]);
}

// 比較表の列。いずれも小さいほど有利な指標で、値は中央値（P50）を使う。
// readは計測結果の言語ごとの値から列の値を取り出す。
const SUMMARY_COLUMNS = [
  {
    key: "cost",
    label: "料金（総額）",
    hint: "100万リクエストあたりの Lambda 料金。リクエスト料金 $0.20 を含みます。",
    read: (values) => values?.cost?.estimated_cost_per_1m,
    format: formatCost,
  },
  {
    key: "compute_cost",
    label: "うち実行時間分",
    hint: "総額からリクエスト料金を除いた部分。言語による差はここに表れます。",
    read: (values) => values?.cost?.estimated_compute_cost_per_1m,
    format: formatCost,
  },
  {
    key: "duration",
    label: "処理時間（普段）",
    hint: "起動済みの実行環境で Lambda が処理した時間。回線の影響を含みません。",
    read: (values) => values?.warm_metrics?.duration_ms?.p50,
    format: (value) => formatValue(value, "ms"),
  },
  {
    key: "init",
    label: "コールドスタート",
    hint: "新しい実行環境の起動にかかった初期化時間。しばらく呼ばれなかった後の最初の応答が遅れる分です。",
    read: (values) => values?.cold_metrics?.init_duration_ms?.p50,
    format: (value) => formatValue(value, "ms"),
  },
  {
    key: "latency",
    label: "応答時間",
    hint: "手元から見た、リクエストから応答までの時間。回線の影響を含みます。",
    read: (values) => values?.metrics?.latency_ms?.p50,
    format: (value) => formatValue(value, "ms"),
  },
  {
    key: "memory",
    label: "メモリ使用量",
    hint: "1回の実行で使ったメモリの最大値。メモリ設定をどこまで下げられるかの目安です。",
    read: (values) => values?.metrics?.max_memory_used_mb?.p50,
    format: (value) => formatValue(value, "MB"),
  },
];

// 列の値で言語を有利な順（小さい順）に並べる。値がない言語は含めない。
function rankBy(run, column) {
  return implementationsOf(run)
    .map((implementation) => ({ ...implementation, value: column.read(run.implementations[implementation.key]) }))
    .filter(({ value }) => isNumber(value))
    .sort((left, right) => left.value - right.value);
}

function formatRatio(value, best) {
  if (!isNumber(value) || !isNumber(best) || best <= 0) return "";
  const ratio = value / best;
  return `×${ratio < 10 ? ratio.toFixed(1) : Math.round(ratio)}`;
}

// 結論のカード。最も有利な言語と、最も不利な言語との差を示す。
function renderVerdict(run) {
  const cards = [
    { column: "cost", title: "最も安い" },
    { column: "duration", title: "普段の処理が最も速い" },
    { column: "init", title: "コールドスタートが最も短い" },
  ].map(({ column: key, title }) => {
    const column = SUMMARY_COLUMNS.find((item) => item.key === key);
    const ranked = rankBy(run, column);
    // 比べられる言語が二つ未満なら、結論を出さずに理由を示す。
    if (ranked.length < 2) {
      return el("div", { class: "card verdict" }, [
        el("p", { class: "verdict-title", text: title }),
        el("p", { class: "verdict-empty", text: key === "init" ? "コールドスタートを含む計測がありません" : "比べられる値がありません" }),
      ]);
    }
    const best = ranked[0];
    const worst = ranked[ranked.length - 1];
    return el("div", { class: "card verdict" }, [
      el("p", { class: "verdict-title", text: title }),
      el("p", { class: "verdict-winner" }, [el("span", { class: `series-key ${best.series}`, text: best.label })]),
      el("p", { class: "verdict-value", text: column.format(best.value) }),
      el("p", {
        class: "verdict-note",
        text: `最も不利な ${worst.label} は ${column.format(worst.value)}（${formatRatio(worst.value, best.value)}）`,
      }),
    ]);
  });
  return el("section", { class: "summary", "aria-labelledby": "verdict-title" }, [
    el("div", { class: "section-head" }, el("h3", { id: "verdict-title", text: "結論" })),
    el("div", { class: "verdicts" }, cards),
  ]);
}

// 言語を行、指標を列に並べた比較表。最も有利な値に★、それ以外に最良との比を添える。
function renderSummaryTable(run) {
  const bestByColumn = Object.fromEntries(SUMMARY_COLUMNS.map((column) => [column.key, rankBy(run, column)[0]?.value]));
  const header = el("tr", {}, [
    el("th", { class: "text", scope: "col", text: "言語" }),
    ...SUMMARY_COLUMNS.map((column) => el("th", { scope: "col", text: column.label })),
  ]);
  const rows = implementationsOf(run).map(({ key, label, series }) => {
    const values = run.implementations[key];
    return el("tr", {}, [
      el("th", { class: "text", scope: "row" }, el("span", { class: `series-key ${series}`, text: label })),
      ...SUMMARY_COLUMNS.map((column) => {
        const value = column.read(values);
        const best = bestByColumn[column.key];
        if (!isNumber(value)) return el("td", { class: "muted", text: column.key === "init" ? "起動なし" : "未計測" });
        const isBest = value === best;
        return el("td", { class: isBest ? "best" : "" }, [
          el("span", { text: column.format(value) }),
          el("span", { class: "cell-note", text: isBest ? "★ 最良" : formatRatio(value, best) }),
        ]);
      }),
    ]);
  });
  return el("div", { class: "card" }, [
    el("div", { class: "section-head" }, el("h3", { text: "言語ごとの比較" })),
    el("p", {
      class: "section-note",
      text: "どの指標も小さいほど有利です。★は最も有利な言語、×は最も有利な値との比です。時間とメモリは中央値です。",
    }),
    el("div", { class: "table-scroll" }, el("table", { class: "summary-table" }, [el("thead", {}, header), el("tbody", {}, rows)])),
    el(
      "dl",
      { class: "metric-guide" },
      SUMMARY_COLUMNS.flatMap((column) => [el("dt", { text: column.label }), el("dd", { text: column.hint })]),
    ),
  ]);
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

function legend(run) {
  return el(
    "div",
    { class: "legend", "aria-label": "凡例" },
    implementationsOf(run).map(({ label, series }) => el("span", { class: `series-key ${series}`, text: label })),
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
  const list = implementationsOf(run);
  const values = PERCENTILES.flatMap(({ key: percentile }) =>
    list.map(({ key }) => statsFor(key)?.[percentile]),
  ).filter(isNumber);

  const width = Math.max(container.clientWidth, 280);
  const margin = { top: 8, right: 72, bottom: 28, left: 44 };
  const barHeight = 18;
  const barGap = 2;
  const groupGap = 20;
  const groupHeight = list.length * barHeight + (list.length - 1) * barGap;
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
    "aria-label": `${metric.label}（${scopeLabel}）のP50・P95・P99の比較`,
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
    list.forEach(({ key, label, series }, index) => {
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
          svg("path", { d: barPath(margin.left, x1, y, barHeight), fill: `var(--${series})` }),
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
  const scope = SCOPES.find(({ key }) => key === state.scope);
  const chart = el("div", { class: "chart" });
  const counts = implementationsOf(run).map(({ key, label }) => {
    const count = run.implementations[key]?.[state.scope]?.[state.metric]?.count ?? 0;
    return `${label} ${count.toLocaleString("ja-JP")} 件`;
  }).join(" / ");

  const card = el("div", { class: "card", id: "comparison" }, [
    el("div", { class: "section-head" }, [el("h3", { text: "指標ごとの詳しい比較" }), legend(run)]),
    el("p", {
      class: "section-note",
      text: "指標と対象の計測を選ぶと、各言語を P50・P95・P99 の3つの位置で比べます。棒が短いほど有利です。",
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
    el("dl", { class: "metric-guide" }, [
      el("dt", { text: "表しているもの" }),
      el("dd", { text: `${metric.description}${scope.description}` }),
      el("dt", { text: "分かること" }),
      el("dd", { text: metric.insight }),
      el("dt", { text: "P50・P95・P99" }),
      el("dd", {
        text: "P50 は普段の値（半分の計測がこれ以下）、P95・P99 はまれに起きる遅い・重いケースの値（95%・99% がこれ以下）です。P50 と P99 の差が小さいほど安定しています。",
      }),
    ]),
    chart,
    el("p", { class: "chart-count", text: `集計した計測の件数: ${counts}` }),
  ]);
  // 描画幅を確定させてからSVGを組み立てる。
  requestAnimationFrame(() => renderChart(chart, run));
  return card;
}

// すべての指標の分布を、言語を列にした数値表で示す。言語が増えても縦に伸びない。
function renderPercentileTable(run) {
  const scopeLabel = SCOPES.find((scope) => scope.key === state.scope).label;
  const languages = implementationsOf(run);
  const header = el("tr", {}, [
    el("th", { class: "text", scope: "col", text: "指標" }),
    el("th", { class: "text", scope: "col", text: "位置" }),
    ...languages.map(({ label, series }) => el("th", { scope: "col" }, el("span", { class: `series-key ${series}`, text: label }))),
  ]);
  const rows = [];
  for (const [metricKey, metric] of Object.entries(METRICS)) {
    for (const percentile of PERCENTILES) {
      rows.push(
        el("tr", {}, [
          el("td", { class: "text", text: percentile.key === "p50" ? `${metric.label}（${metric.unit}）` : "" }),
          el("td", { class: "text", text: `${percentile.label}（${percentile.description}）` }),
          ...languages.map(({ key }) =>
            el("td", { text: formatNumber(run.implementations[key]?.[state.scope]?.[metricKey]?.[percentile.key]) }),
          ),
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
  const rows = implementationsOf(run).map(({ key, label }) => {
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
      text: "各指標が何件の計測から求めたかを示します。コールドは実行環境の起動を伴った呼び出し、ウォームは起動済みの環境での呼び出しです。「ログと未結合」が 0 件でないと、Lambda 側の指標と料金が欠けます。",
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
  const metadata = run.metadata;
  const workload = metadata.workload ?? {};
  const conditions = [
    workload.method && workload.path ? `${workload.method} ${workload.path}` : null,
    formatDate(metadata.started_at),
    metadata.architecture,
    metadata.memory_mb ? `${metadata.memory_mb} MB` : null,
    workload.iterations ? `各言語 ${workload.iterations} 回` : null,
  ].filter(Boolean);
  // 結論と比較表を先に、詳しいグラフと根拠のデータを後ろに置く。
  detail.replaceChildren(
    ...[
      el("div", { class: "detail-head" }, [
        el("h2", { text: `${workload.name ?? "ワークロード不明"} の比較結果` }),
        el("p", { text: conditions.join(" · ") }),
      ]),
      renderNotice(run),
      run.unsupported?.length
        ? el("p", {
            class: "section-note",
            text: `このワークロードは ${run.unsupported.join("・")} に未対応のため、計測していません。`,
          })
        : null,
      renderVerdict(run),
      renderSummaryTable(run),
      renderComparison(run),
      renderPercentileTable(run),
      renderSampleTable(run),
      renderConditions(run),
      renderImageMetrics(run),
    ].filter(Boolean),
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


// --- 計測の開始 ------------------------------------------------------------

const COUNT_FIELDS = [
  { key: "iterations", label: "反復回数（各実装）", unit: "回", help: "各言語にそれぞれ送る計測用のリクエスト数です。" },
  { key: "client_concurrency", label: "同時実行数", unit: "", help: "同時に送るリクエスト数です。" },
  { key: "warmup", label: "ウォームアップ（各実装）", unit: "回", help: "計測前に送る、結果に含めないリクエスト数です。" },
];

function formatElapsed(seconds) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function stepLabel(job) {
  return job.steps.find((step) => step.key === job.step)?.label ?? job.step;
}

// 入力欄の値を検証し、範囲外なら理由を返す。
function validateCount(value, limit) {
  if (value === "" || !/^\d+$/.test(value)) return "整数で入力してください。";
  const number = Number(value);
  if (number < limit.min || number > limit.max) return `${limit.min} 以上 ${limit.max} 以下で入力してください。`;
  return null;
}

function costEstimate(workload, iterations, warmup, targets) {
  const basis = workload.cost_basis;
  const missing = targets.filter((key) => !isNumber(basis?.cost_per_request?.[key]));
  // 選んだ言語のいずれかに同じワークロードの実測がなければ、見込みを出さない。
  if (!basis || missing.length > 0) {
    return "料金の見込み: 選んだ言語の一部に、同じワークロードの比較に使える実測がないため、算出できません。";
  }
  const perImplementation = iterations + warmup;
  const total = targets.reduce((sum, key) => sum + basis.cost_per_request[key] * perImplementation, 0);
  return `Lambda 料金の見込み: 約 ${formatCost(total)}（${formatDate(basis.started_at)} の同じワークロードの実測から算出。API Gateway・DynamoDB・CloudWatch は含みません）`;
}

function renderLaunchForm(options) {
  const { settings, workloads, limits } = options;
  const running = state.job?.state === "running";
  const form = el("form", { class: "launch-form", novalidate: "" });

  // 固定条件。Lambda の実設定と食い違うと比較が無効になるため、画面では変更させない。
  const fixed = el("dl", { class: "conditions" }, [
    el("dt", { text: "アーキテクチャ" }),
    el("dd", { text: formatOptional(settings.architecture) }),
    el("dt", { text: "Lambda メモリ" }),
    el("dd", { text: formatOptional(settings.memory_mb, " MB") }),
    el("dt", { text: "リージョン" }),
    el("dd", { text: settings.region }),
  ]);

  const workloadSelect = el(
    "select",
    { id: "launch-workload", name: "workload" },
    workloads.map((workload) =>
      el("option", { value: workload.name, text: `${workload.name}（${workload.method} ${workload.path}）` }),
    ),
  );

  const inputs = {};
  const errors = {};
  const fields = COUNT_FIELDS.map((field) => {
    const limit = limits[field.key];
    inputs[field.key] = el("input", {
      id: `launch-${field.key}`,
      type: "number",
      inputmode: "numeric",
      min: String(limit.min),
      max: String(limit.max),
      step: "1",
      required: "",
      "aria-describedby": `launch-${field.key}-help launch-${field.key}-error`,
    });
    errors[field.key] = el("p", { id: `launch-${field.key}-error`, class: "field-error", role: "alert" });
    return el("div", { class: "field" }, [
      el("label", { for: `launch-${field.key}`, text: field.label }),
      el("div", { class: "field-input" }, [inputs[field.key], field.unit ? el("span", { class: "muted", text: field.unit }) : null]),
      el("p", { id: `launch-${field.key}-help`, class: "field-help", text: `${field.help}（${limit.min}〜${limit.max}）` }),
      errors[field.key],
    ]);
  });

  // 計測する言語。既定は全言語で、比較のため二つ以上を選ぶ。
  const languageInputs = settings.implementations.map(({ key, label }, index) =>
    el("label", { class: "check" }, [
      el("input", { type: "checkbox", name: "targets", value: key, checked: "" }),
      el("span", { class: `series-key series-${(index % 6) + 1}`, text: label }),
      el("span", { class: "check-note muted" }),
    ]),
  );
  const languageError = el("p", { id: "launch-targets-error", class: "field-error", role: "alert" });
  const selectedTargets = () =>
    languageInputs.map((label) => label.querySelector("input")).filter((input) => input.checked).map((input) => input.value);

  const summary = el("p", { class: "launch-summary" });
  const estimate = el("p", { class: "field-help" });
  const submitError = el("p", { class: "field-error", role: "alert" });
  const submit = el("button", { type: "submit", class: "primary-button", text: "計測を開始" });

  const workloadDescription = el("p", { class: "field-help" });
  const applyDefaults = () => {
    const workload = workloads.find((item) => item.name === workloadSelect.value);
    for (const field of COUNT_FIELDS) inputs[field.key].value = String(workload[field.key] ?? "");
    workloadDescription.textContent = workload.description ?? "";
    // ワークロードが対応していない言語は選べないようにし、「未対応」と添える。
    for (const label of languageInputs) {
      const input = label.querySelector("input");
      const supported = !workload.implementations || workload.implementations.includes(input.value);
      input.disabled = !supported;
      input.checked = supported;
      label.querySelector(".check-note").textContent = supported ? "" : "（未対応）";
    }
    update();
  };

  // 入力のたびに検証と見込みを更新し、開始前に送信内容を確認できるようにする。
  const update = () => {
    let valid = settings.ready && !running;
    const targets = selectedTargets();
    languageError.textContent = targets.length < 2 ? "比べる言語を二つ以上選んでください。" : "";
    if (targets.length < 2) valid = false;
    for (const field of COUNT_FIELDS) {
      const message = validateCount(inputs[field.key].value, limits[field.key]);
      errors[field.key].textContent = message ?? "";
      inputs[field.key].setAttribute("aria-invalid", String(Boolean(message)));
      if (message) valid = false;
    }
    submit.disabled = !valid;
    if (!valid && settings.ready && !running) {
      summary.textContent = "入力を確認してください。";
      estimate.textContent = "";
      return;
    }
    const iterations = Number(inputs.iterations.value);
    const warmup = Number(inputs.warmup.value);
    const perImplementation = iterations + warmup;
    const languages = settings.implementations.filter(({ key }) => targets.includes(key));
    summary.textContent = `${languages.map(({ label }) => label).join("・")} にそれぞれ ${perImplementation.toLocaleString("ja-JP")} 回（ウォームアップ ${warmup.toLocaleString("ja-JP")} 回を含む）、合計 ${(perImplementation * languages.length).toLocaleString("ja-JP")} リクエストを送ります。`;
    const workload = workloads.find((item) => item.name === workloadSelect.value);
    estimate.textContent = costEstimate(workload, iterations, warmup, targets);
  };

  workloadSelect.addEventListener("change", applyDefaults);
  for (const label of languageInputs) label.querySelector("input").addEventListener("change", update);
  for (const field of COUNT_FIELDS) inputs[field.key].addEventListener("input", update);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    update();
    if (submit.disabled) return;
    submit.disabled = true;
    submit.textContent = "開始しています…";
    submitError.textContent = "";
    try {
      const { job } = await fetchJson("/api/jobs", {
        workload: workloadSelect.value,
        iterations: Number(inputs.iterations.value),
        client_concurrency: Number(inputs.client_concurrency.value),
        warmup: Number(inputs.warmup.value),
        targets: selectedTargets(),
      });
      setJob(job);
    } catch (error) {
      submitError.textContent = error.message;
      submit.textContent = "計測を開始";
      update();
    }
  });

  form.append(
    el("div", { class: "field" }, [
      el("label", { for: "launch-workload", text: "ワークロード" }),
      workloadSelect,
      workloadDescription,
      el("p", { class: "field-help", text: "選ぶと回数の既定値が入ります。" }),
    ]),
    el("fieldset", { class: "field" }, [
      el("legend", { text: "計測する言語" }),
      el("div", { class: "checks", "aria-describedby": "launch-targets-error" }, languageInputs),
      languageError,
    ]),
    el("div", { class: "field-grid" }, fields),
    el("div", { class: "launch-confirm" }, [summary, estimate]),
    submitError,
    el("div", { class: "form-actions" }, [
      submit,
      el("p", {
        class: "field-help",
        text: `開始後は、ログの反映を ${options.log_wait_seconds} 秒待ってから、ログ収集とレポート作成まで自動で行います。`,
      }),
    ]),
  );

  const cards = [];
  if (!settings.ready) {
    cards.push(
      el("div", { class: "card notice" }, [
        el("h3", { class: "badge badge-warning", text: "計測の準備ができていません" }),
        el("ul", {}, settings.missing.map((item) => el("li", { text: item }))),
        el("p", {
          class: "section-note",
          text: "接続先と条件は、Terraform の出力と AWS 上の Lambda の設定から自動で読み取ります。原因を解消したら、画面を起動し直さずに再確認できます。",
        }),
        el("div", { class: "form-actions" }, [
          el("button", { type: "button", class: "ghost-button", text: "設定を再確認", onclick: openLaunch }),
        ]),
      ]),
    );
  }
  cards.push(
    el("div", { class: "card" }, [
      el("div", { class: "section-head" }, el("h3", { text: "固定の条件" })),
      el("p", {
        class: "section-note",
        text: "AWS 上の Lambda から読み取った実際の設定です。この条件で計測し、結果に記録します。",
      }),
      fixed,
    ]),
    el("div", { class: "card" }, [
      el("div", { class: "section-head" }, el("h3", { text: "計測の内容" })),
      running
        ? el("p", { class: "section-note", text: "計測の実行中は、新しい計測を開始できません。" })
        : null,
      form,
    ]),
  );
  // 既定値の反映は要素の生成後に行う。
  queueMicrotask(applyDefaults);
  return cards;
}

function renderJobPanel(job) {
  const currentIndex = job.steps.findIndex((step) => step.key === job.step);
  const finished = job.state !== "running";
  const steps = el(
    "ol",
    { class: "job-steps" },
    job.steps.map((step, index) => {
      // 成功時は全工程を完了、実行中・失敗・中止時は現在の工程より前だけを完了として示す。
      let status = "pending";
      if (job.state === "succeeded" || index < currentIndex) status = "done";
      else if (index === currentIndex) status = job.state === "running" ? "current" : job.state;
      const labels = { done: "完了", current: "実行中", pending: "待機", failed: "失敗", cancelled: "中止" };
      return el("li", { class: `job-step job-step-${status}` }, [
        el("span", { class: "job-step-mark", "aria-hidden": "true" }),
        el("span", { text: step.label }),
        el("span", { class: "muted", text: labels[status] }),
      ]);
    }),
  );

  const headings = {
    running: { class: "badge badge-running", text: "計測を実行中です" },
    succeeded: { class: "badge badge-good", text: "計測が完了しました" },
    failed: { class: "badge badge-error", text: "計測に失敗しました" },
    cancelled: { class: "badge badge-warning", text: "計測を中止しました" },
  };
  const actions = [];
  if (job.state === "running") {
    actions.push(el("button", { type: "button", class: "ghost-button", text: "中止する", onclick: cancelJob }));
  }
  if (job.state === "succeeded") {
    actions.push(
      el("button", {
        type: "button",
        class: "primary-button",
        text: "結果を見る",
        onclick: () => {
          // 結果を開いたら案内は済んだため、次に計測開始画面を開いたときは出さない。
          state.watchedRunId = null;
          loadRuns(job.run_id);
        },
      }),
    );
  }

  return el("div", { class: `card notice job-panel job-${job.state}`, id: "job-panel" }, [
    el("h3", headings[job.state]),
    el("p", {
      class: "section-note",
      text: `${job.workload} · 合計 ${job.total_requests.toLocaleString("ja-JP")} リクエスト · 経過 ${formatElapsed(job.elapsed_seconds)}`,
    }),
    steps,
    job.message ? el("p", { text: job.message }) : null,
    finished && job.state !== "succeeded" && job.output.length
      ? el("details", {}, [el("summary", { text: "コマンド出力の末尾" }), el("pre", { text: job.output.join("\n") })])
      : null,
    actions.length ? el("div", { class: "form-actions" }, actions) : null,
  ]);
}

// 計測開始画面に出す計測は、実行中のものか、この画面で実行中から見届けたものに限る。
// サーバーは前回の計測を保持し続けるため、以前に終わった計測は出さない。
function isJobRelevant(job) {
  return job.state === "running" || job.run_id === state.watchedRunId;
}

function renderLaunch() {
  hideTooltip();
  const children = [
    el("div", { class: "detail-head" }, [
      el("h2", { text: "新しく計測する" }),
      el("p", { text: "AWS 上の各言語の Lambda に同じリクエストを送り、結果を比較できる形で保存します。" }),
    ]),
  ];
  if (state.job && isJobRelevant(state.job)) children.push(renderJobPanel(state.job));
  if (state.launch) children.push(...renderLaunchForm(state.launch));
  else children.push(el("p", { class: "muted", text: "AWS 上の設定を確認しています…" }));
  detail.replaceChildren(...children);
}

function renderJobBanner() {
  const job = state.job;
  // 計測開始画面を開いていない間だけ、実行中の計測を画面上部で知らせる。
  if (!job || job.state !== "running" || state.view === "launch") {
    jobBanner.hidden = true;
    return;
  }
  jobBanner.replaceChildren(
    el("span", { class: "badge badge-running", text: "計測を実行中" }),
    el("span", { text: `${job.workload} · ${stepLabel(job)} · 経過 ${formatElapsed(job.elapsed_seconds)}` }),
    el("button", { type: "button", class: "ghost-button", text: "進み具合を見る", onclick: openLaunch }),
  );
  jobBanner.hidden = false;
}

let jobTimer = null;

// 計測の状態を反映し、実行中なら一定間隔で問い合わせ、終わったら計測履歴を更新する。
function setJob(job) {
  const wasRunning = state.job?.state === "running";
  state.job = job;
  // 実行中の計測は、終わったときに結果を案内できるよう見届け対象として覚える。
  if (job?.state === "running") state.watchedRunId = job.run_id;
  clearTimeout(jobTimer);
  if (job?.state === "running") jobTimer = setTimeout(pollJob, JOB_POLL_MS);
  if (wasRunning && job && job.state !== "running") loadRuns(state.selectedId, { keepView: true });
  renderJobBanner();
  if (state.view !== "launch") return;
  const panel = document.getElementById("job-panel");
  // 実行中かどうかが変わらない間は状態パネルだけを差し替え、入力中のフォームを保つ。
  if (panel && job && wasRunning === (job.state === "running")) {
    panel.replaceWith(renderJobPanel(job));
  } else {
    renderLaunch();
  }
}

async function pollJob() {
  try {
    const { job } = await fetchJson("/api/jobs/current");
    setJob(job);
  } catch {
    // サーバーへ一時的に接続できなくても、次の問い合わせで回復できるよう再試行する。
    jobTimer = setTimeout(pollJob, JOB_POLL_MS * 2);
  }
}

async function cancelJob() {
  try {
    const { job } = await fetchJson("/api/jobs/current/cancel", {});
    setJob(job);
  } catch (error) {
    renderError(detail, error.message, openLaunch);
  }
}

async function openLaunch() {
  state.view = "launch";
  state.selectedId = null;
  history.replaceState(null, "", "#launch");
  renderRunList();
  renderJobBanner();
  // 設定の読み取りには数秒かかるため、古い内容を出さずに確認中であることを示す。
  state.launch = null;
  renderLaunch();
  try {
    state.launch = await fetchJson("/api/launch");
    state.job = state.launch.job;
    // 設定を読み込んだらフォームを含む画面全体を描き、その後に実行中の計測の問い合わせを始める。
    renderLaunch();
    setJob(state.job);
  } catch (error) {
    renderError(detail, error.message, openLaunch);
  }
}

// --- 作りたいAPIの見込み ------------------------------------------------------

// 入力が止まってから計算する待ち時間。入力のたびに計算を送らないようにする。
const ESTIMATE_DEBOUNCE_MS = 300;

function formatMonthlyCost(value) {
  if (!isNumber(value)) return "未計算";
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function renderEstimateResult(result) {
  const estimable = result.languages.filter((language) => language.estimable);
  const ranked = [...estimable].sort((left, right) => left.monthly_cost - right.monthly_cost);
  const best = ranked[0];
  const series = Object.fromEntries(result.languages.map(({ key }, index) => [key, `series-${(index % 6) + 1}`]));
  const header = el("tr", {}, [
    el("th", { class: "text", scope: "col", text: "言語" }),
    el("th", { scope: "col", text: "月額料金（見込み）" }),
    el("th", { scope: "col", text: "うち実行時間分" }),
    el("th", { scope: "col", text: "処理時間（普段・平均）" }),
    el("th", { scope: "col", text: "コールドスタート時の追加" }),
  ]);
  const rows = [
    ...ranked.map((language) =>
      el("tr", {}, [
        el("th", { class: "text", scope: "row" }, el("span", { class: `series-key ${series[language.key]}`, text: language.label })),
        el("td", { class: language === best ? "best" : "" }, [
          el("span", { text: formatMonthlyCost(language.monthly_cost) }),
          el("span", { class: "cell-note", text: language === best ? "★ 最も安い" : formatRatio(language.monthly_cost, best.monthly_cost) }),
        ]),
        el("td", { text: formatMonthlyCost(language.monthly_compute_cost) }),
        el("td", { text: formatValue(language.warm_duration_ms, "ms") }),
        el("td", { text: isNumber(language.init_duration_ms) ? `+${formatValue(language.init_duration_ms, "ms")}` : "実測なし" }),
      ]),
    ),
    // 実測が足りない言語は、見込みを出さずに理由と対処を示す。
    ...result.languages
      .filter((language) => !language.estimable)
      .map((language) =>
        el("tr", {}, [
          el("th", { class: "text", scope: "row" }, el("span", { class: `series-key ${series[language.key]}`, text: language.label })),
          el("td", {
            colspan: "4",
            class: "text muted",
            text: `見込めません: ${language.missing_workloads.join("・")} の実測がありません（この言語を含めて計測すると見込めます）`,
          }),
        ]),
      ),
  ];

  const notes = [
    "Lambda の料金（リクエスト料金と実行時間分）だけの見込みです。API Gateway・DynamoDB・データ転送・Free Tier は含みません。",
    `料金表: ${result.pricing.region} ${result.pricing.updated_at} 時点。実測の平均を構成割合で重み付けしています。`,
  ];
  const approximated = estimable.filter((language) => language.approximated_cold_workloads.length);
  if (approximated.length) {
    notes.push(
      "コールドスタートの実測がないワークロードは、その言語の初期化時間（中央値）の分の料金を足して近似しています。",
    );
  }
  const sources = estimable.map((language) =>
    el("li", {
      text: `${language.label}: ${language.sources.map((source) => `${source.workload}（${formatDate(source.started_at)}）`).join("、")}`,
    }),
  );

  return [
    el("div", { class: "table-scroll" }, el("table", { class: "summary-table" }, [el("thead", {}, header), el("tbody", {}, rows)])),
    el("ul", { class: "estimate-notes" }, notes.map((text) => el("li", { text }))),
    el("details", {}, [el("summary", { text: "根拠にした計測" }), el("ul", {}, sources)]),
  ];
}

function renderEstimateForm(options) {
  const { workloads, conditions } = options;
  if (conditions.length === 0) {
    return el("div", { class: "card" }, [
      el("h3", { text: "見込みに使える計測がありません" }),
      el("p", { class: "section-note", text: "「新しく計測する」で、見込みたい処理に近いワークロードを計測してください。" }),
    ]);
  }

  const condition = el(
    "select",
    { id: "estimate-condition" },
    conditions.map((item, index) =>
      el("option", {
        value: String(index),
        text: `${item.architecture} · ${item.memory_mb} MB（計測 ${item.run_count} 件）`,
      }),
    ),
  );
  const shares = {};
  const shareRows = workloads.map((workload, index) => {
    // 初期値は最初のワークロードを100%にし、合計100%の状態から始める。
    shares[workload.name] = el("input", {
      type: "number",
      inputmode: "numeric",
      min: "0",
      max: "100",
      step: "1",
      value: index === 0 ? "100" : "0",
      "aria-label": `${workload.name} の割合`,
    });
    return el("tr", {}, [
      el("td", { class: "text" }, [el("strong", { text: workload.name }), el("span", { class: "cell-note", text: workload.description ?? "" })]),
      el("td", {}, el("div", { class: "field-input" }, [shares[workload.name], el("span", { class: "muted", text: "%" })])),
    ]);
  });
  const total = el("p", { class: "launch-summary", role: "status" });
  const monthly = el("input", { id: "estimate-monthly", type: "number", inputmode: "numeric", min: "1", step: "1", value: "1000000" });
  const cold = el("input", { id: "estimate-cold", type: "number", inputmode: "decimal", min: "0", max: "100", step: "0.1", value: "1" });
  const error = el("p", { class: "field-error", role: "alert" });
  const output = el("div", { class: "estimate-output", "aria-live": "polite" });

  let timer = null;
  let sequence = 0;
  const calculate = async () => {
    const mix = Object.fromEntries(Object.entries(shares).map(([name, input]) => [name, Number(input.value || 0)]));
    const sum = Object.values(mix).reduce((left, right) => left + right, 0);
    total.textContent = `割合の合計: ${sum}%${sum === 100 ? "" : "（100% にしてください）"}`;
    error.textContent = "";
    // 合計が100%でない間は計算を送らず、直前の結果も消して誤読を防ぐ。
    if (sum !== 100) {
      output.replaceChildren();
      return;
    }
    const selected = conditions[Number(condition.value)];
    const current = ++sequence;
    output.replaceChildren(el("p", { class: "muted", text: "計算しています…" }));
    try {
      const result = await fetchJson("/api/estimate", {
        mix,
        monthly_requests: Number(monthly.value),
        cold_start_percent: Number(cold.value),
        architecture: selected.architecture,
        memory_mb: selected.memory_mb,
      });
      // 計算中に入力が変わっていたら、古い結果で表示を上書きしない。
      if (current === sequence) output.replaceChildren(...renderEstimateResult(result));
    } catch (failure) {
      if (current === sequence) {
        output.replaceChildren();
        error.textContent = failure.message;
      }
    }
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(calculate, ESTIMATE_DEBOUNCE_MS);
  };
  for (const input of [condition, monthly, cold, ...Object.values(shares)]) input.addEventListener("input", schedule);
  queueMicrotask(calculate);

  return el("div", { class: "card" }, [
    el("div", { class: "section-head" }, el("h3", { text: "作りたいAPIの処理の構成" })),
    el("p", {
      class: "section-note",
      text: "作りたいAPIのリクエストが、どの処理にどれくらいの割合で近いかを入力してください。計測済みの結果を割合で重み付けして見込みます。",
    }),
    el("div", { class: "table-scroll" }, el("table", { class: "estimate-mix" }, el("tbody", {}, shareRows))),
    total,
    el("div", { class: "field-grid" }, [
      el("div", { class: "field" }, [
        el("label", { for: "estimate-monthly", text: "月間リクエスト数" }),
        monthly,
        el("p", { class: "field-help", text: "1か月に受けるリクエストの数です。" }),
      ]),
      el("div", { class: "field" }, [
        el("label", { for: "estimate-cold", text: "コールドスタートの割合" }),
        el("div", { class: "field-input" }, [cold, el("span", { class: "muted", text: "%" })]),
        el("p", {
          class: "field-help",
          text: "新しい実行環境の起動を伴うリクエストの割合です。目安: 常にアクセスがある 0.1%、ときどき 1〜5%、まれ 10%以上。",
        }),
      ]),
      el("div", { class: "field" }, [
        el("label", { for: "estimate-condition", text: "Lambda の条件" }),
        condition,
        el("p", { class: "field-help", text: "この条件で計測した結果だけを使います。" }),
      ]),
    ]),
    error,
    el("div", { class: "section-head" }, el("h3", { text: "言語ごとの見込み（安い順）" })),
    output,
  ]);
}

async function openEstimate() {
  state.view = "estimate";
  state.selectedId = null;
  history.replaceState(null, "", "#estimate");
  renderRunList();
  renderJobBanner();
  hideTooltip();
  const head = el("div", { class: "detail-head" }, [
    el("h2", { text: "作りたいAPIの料金を見込む" }),
    el("p", { text: "計測済みのワークロードを組み合わせて、言語ごとの月額料金と処理時間を見込みます。実績ではなく見込みです。" }),
  ]);
  detail.replaceChildren(head, el("p", { class: "muted", text: "読み込み中…" }));
  try {
    const options = await fetchJson("/api/estimate/options");
    if (state.view !== "estimate") return;
    detail.replaceChildren(head, renderEstimateForm(options));
  } catch (failure) {
    renderError(detail, failure.message, openEstimate);
  }
}

// --- 状態遷移 ----------------------------------------------------------------

async function selectRun(runId) {
  // 計測開始画面で終わった計測を表示した後に離れる場合は、案内は済んだものとして次から出さない。
  if (state.view === "launch" && state.job && state.job.state !== "running") state.watchedRunId = null;
  state.view = "run";
  state.selectedId = runId;
  renderJobBanner();
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

// 計測履歴を読み直す。preferredに指定したrun、URLのrun、最新のrunの順で開く。
// keepViewなら計測開始画面を開いたまま履歴だけを更新する。
async function loadRuns(preferred = null, { keepView = false } = {}) {
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
  if (keepView && state.view !== "run") return;
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
  const requested = typeof preferred === "string" ? preferred : runIdFromHash();
  const initial = state.runs.some((run) => run.run_id === requested) ? requested : state.runs[0].run_id;
  await selectRun(initial);
}

document.getElementById("reload").addEventListener("click", () => loadRuns(state.selectedId, { keepView: true }));
document.getElementById("open-launch").addEventListener("click", openLaunch);
document.getElementById("open-estimate").addEventListener("click", openEstimate);

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  // 計測結果の表示中だけ、グラフを新しい幅で描き直す。
  resizeTimer = setTimeout(() => state.view === "run" && renderDetail(), 150);
});

// 起動時は実行中の計測を確認し、URLが#launchなら計測開始画面、それ以外は計測結果を開く。
fetchJson("/api/jobs/current")
  .then(({ job }) => setJob(job))
  .catch(() => {});
if (location.hash === "#launch") {
  loadRuns(null, { keepView: true });
  openLaunch();
} else if (location.hash === "#estimate") {
  loadRuns(null, { keepView: true });
  openEstimate();
} else {
  loadRuns();
}
