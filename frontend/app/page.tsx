
"use client";

import {
  ChangeEvent,
  FormEvent,
  useMemo,
  useState,
} from "react";

type KPI = {
  name: string;
  value: number | string;
  unit: string;
  description: string;
};

type PerformanceItem = {
  product?: string;
  model?: string;
  component?: string;
  supplier?: string;
  batch?: string;
  production: number;
  defects: number;
  defect_rate: number;
  downtime: number;
};

type DefectTypeAnalysis = {
  defect_type: string;
  defects: number;
};

type DailyProduction = {
  date: string;
  production: number;
  defects: number;
  defect_rate: number;
  downtime: number;
};

type DataScope = {
  has_product_data: boolean;
  has_model_data: boolean;
  has_component_data: boolean;
  has_supplier_data: boolean;
  has_batch_data: boolean;
  has_defect_type_data: boolean;
  has_quality_status_data: boolean;
  has_defect_data: boolean;
  has_downtime_data: boolean;
  has_date_data: boolean;
};

type DataQuality = {
  quality_score: number;
  quality_status: "good" | "warning" | "poor";
  scoring_basis: string;
  total_rows: number;
  data_rows: number;
  valid_rows: number;
  invalid_rows: number;
  empty_rows: number;
  duplicate_rows: number;
  missing_values: Record<string, number>;
  invalid_values: Record<string, number>;
  negative_values: Record<string, number>;
  missing_columns: string[];
  warnings: string[];
};

type DataProcessing = {
  rows_after_deduplication: number;
  rows_used_for_kpi: number;
  invalid_metric_rows_excluded: number;
  invalid_metric_rows_excluded_by_metric: Record<string, number>;
};

type GroupExtreme = {
  entity: string;
  value: number;
  unit: "percent" | "minutes" | "units";
  comparison_count: number;
};

type DailyChangeFinding = {
  detected: boolean;
  from_date?: string;
  to_date?: string;
  previous_production?: number;
  current_production?: number;
  change?: number;
  direction?: "increase" | "decrease" | "unchanged";
  reason?: string;
};

type BatchOutlierFinding = {
  entity: string;
  defect_rate: number;
};

type BatchAnomalyFinding = {
  detected: boolean;
  outliers: BatchOutlierFinding[];
  reason?: string;
};

type ModelDifferenceFinding = {
  detected: boolean;
  lowest_model?: string;
  lowest_defect_rate?: number;
  highest_model?: string;
  highest_defect_rate?: number;
  spread_percentage_points?: number;
  reason?: string;
};

type AnomalyAnalysis = {
  status: "completed" | "insufficient_data";
  findings: {
    highest_defect_rate?: Record<string, GroupExtreme>;
    highest_downtime?: Record<string, GroupExtreme>;
    lowest_production?: Record<string, GroupExtreme>;
    largest_daily_change?: DailyChangeFinding;
    unusual_batch_defect_rate?: BatchAnomalyFinding;
    model_quality_difference?: ModelDifferenceFinding;
  };
};

type AnalysisSection = {
  summary: string;
  key_observations: string[];
};

type EvidenceKey =
  | "production"
  | "defects"
  | "defect_rate"
  | "downtime"
  | "quality_score";

type RiskArea = {
  entity_type:
    | "product"
    | "model"
    | "component"
    | "supplier"
    | "batch"
    | "defect_type"
    | "date"
    | "data_quality";
  entity_name: string;
  risk_level: "low" | "medium" | "high" | "critical";
  evidence_keys: EvidenceKey[];
  observation: string;
  investigation_points: string[];
  evidence: {
    production?: number;
    defects?: number;
    defect_rate?: number;
    downtime?: number;
    quality_score?: number;
  };
};

type RecommendedAction = {
  priority: "low" | "medium" | "high" | "critical";
  action: string;
  target_type: string;
  target_name: string;
  reason: string;
};

type AIAnalysis = {
  executive_summary: AnalysisSection;
  production_analysis: AnalysisSection;
  quality_analysis: AnalysisSection;
  component_analysis: AnalysisSection;
  supplier_analysis: AnalysisSection;
  batch_analysis: AnalysisSection;
  downtime_analysis: AnalysisSection;
  risk_areas: RiskArea[];
  recommended_actions: RecommendedAction[];
};

type HardwareResult = {
  title: string;
  summary: string;

  kpis: KPI[];

  insights: string[];
  warnings: string[];
  recommendations: string[];

  product_performance: PerformanceItem[];
  model_performance: PerformanceItem[];
  component_performance: PerformanceItem[];
  supplier_performance: PerformanceItem[];
  batch_performance: PerformanceItem[];

  defect_type_analysis: DefectTypeAnalysis[];

  daily_production: DailyProduction[];

  data_scope: DataScope;

  data_quality: DataQuality;
  data_processing: DataProcessing;
  anomaly_analysis: AnomalyAnalysis;
  ai_analysis: AIAnalysis;
};

type ComparedModel = {
  name: string;
  production: number;
  defects: number;
  defect_rate: number | null;
  downtime: number;
  rows_used_for_kpi: number;
  invalid_rows_excluded: number;
};

type HardwareComparison = {
  models: {
    model_a: ComparedModel;
    model_b: ComparedModel;
  };
  differences: {
    production: number;
    defects: number;
    defect_rate_percentage_points: number | null;
    downtime: number;
  };
  rate_comparison_status: "available" | "insufficient_production";
  higher_defect_rate_model: string | null;
  ai_analysis: {
    summary: string;
    investigation_points: string[];
  } | null;
  ai_status: "available" | "unavailable";
  ai_error?: string;
};

export default function Home() {
  const [selectedFile, setSelectedFile] =
    useState<File | null>(null);

  const [result, setResult] =
    useState<HardwareResult | null>(null);

  const [loading, setLoading] =
    useState(false);

  const [error, setError] =
    useState("");

  const [selectedModelA, setSelectedModelA] =
    useState("");

  const [selectedModelB, setSelectedModelB] =
    useState("");

  const [comparison, setComparison] =
    useState<HardwareComparison | null>(null);

  const [comparisonLoading, setComparisonLoading] =
    useState(false);

  const [comparisonError, setComparisonError] =
    useState("");

  // --------------------------------
  // File
  // --------------------------------

  const handleFileChange = (
    event: ChangeEvent<HTMLInputElement>
  ) => {
    const file =
      event.target.files?.[0];

    if (!file) {
      return;
    }

    const extension =
      "." +
      file.name
        .split(".")
        .pop()
        ?.toLowerCase();

    if (
      ![".csv", ".xlsx"].includes(
        extension || ""
      )
    ) {
      setError(
        "Chỉ hỗ trợ file CSV hoặc XLSX."
      );

      event.target.value = "";
      return;
    }

    if (
      file.size >
      10 * 1024 * 1024
    ) {
      setError(
        "File không được vượt quá 10MB."
      );

      event.target.value = "";
      return;
    }

    setSelectedFile(file);
    setResult(null);
    setComparison(null);
    setSelectedModelA("");
    setSelectedModelB("");
    setError("");
    setComparisonError("");
  };

  // --------------------------------
  // Analyze
  // --------------------------------

  const handleSubmit = async (
    event: FormEvent<HTMLFormElement>
  ) => {
    event.preventDefault();

    if (!selectedFile) {
      setError(
        "Vui lòng chọn file CSV hoặc XLSX."
      );
      return;
    }

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const formData =
        new FormData();

      formData.append(
        "file",
        selectedFile
      );

      const response =
        await fetch(
          "http://127.0.0.1:8000/api/hardware/analyze",
          {
            method: "POST",
            body: formData,
          }
        );

      if (!response.ok) {
        let message =
          "Không thể phân tích dữ liệu phần cứng.";

        try {
          const data =
            await response.json();

          if (data.detail) {
            message = data.detail;
          }
        } catch {
          // Ignore
        }

        throw new Error(message);
      }

      const data: HardwareResult =
        await response.json();

      setResult(data);
      const availableModels = Array.from(
        new Set(
          data.model_performance
            .map((item) => item.model)
            .filter(
              (model): model is string =>
                Boolean(model && model !== "Không xác định")
            )
        )
      );
      setSelectedModelA(availableModels[0] || "");
      setSelectedModelB(availableModels[1] || "");

      setTimeout(() => {
        document
          .getElementById(
            "dashboard"
          )
          ?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
      }, 100);
    } catch (error) {
      console.error(error);

      if (error instanceof Error) {
        setError(error.message);
      } else {
        setError(
          "Đã xảy ra lỗi không xác định."
        );
      }
    } finally {
      setLoading(false);
    }
  };

  const handleCompare = async () => {
    if (!selectedFile) {
      setComparisonError(
        "Vui lòng chọn file dữ liệu để so sánh model."
      );
      return;
    }

    if (!selectedModelA || !selectedModelB) {
      setComparisonError("Vui lòng chọn đủ hai model.");
      return;
    }

    if (selectedModelA === selectedModelB) {
      setComparisonError("Hai model được chọn phải khác nhau.");
      return;
    }

    setComparisonLoading(true);
    setComparisonError("");
    setComparison(null);

    try {
      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("model_a", selectedModelA);
      formData.append("model_b", selectedModelB);

      const response = await fetch(
        "http://127.0.0.1:8000/api/hardware/compare",
        {
          method: "POST",
          body: formData,
        }
      );
      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "Không thể so sánh hai model."
        );
      }

      setComparison(data as HardwareComparison);
    } catch (error) {
      console.error(error);
      setComparisonError(
        error instanceof Error
          ? error.message
          : "Đã xảy ra lỗi khi so sánh model."
      );
    } finally {
      setComparisonLoading(false);
    }
  };

  // --------------------------------
  // Clear
  // --------------------------------

  const handleClear = () => {
    setSelectedFile(null);
    setResult(null);
    setComparison(null);
    setSelectedModelA("");
    setSelectedModelB("");
    setError("");
    setComparisonError("");

    const input =
      document.getElementById(
        "hardware-file"
      ) as HTMLInputElement | null;

    if (input) {
      input.value = "";
    }
  };

  // --------------------------------
  // KPI helpers
  // --------------------------------

  const getKPI = (
    name: string
  ) => {
    return result?.kpis.find(
      (kpi) =>
        kpi.name === name
    );
  };

  const getKPIByNames = (
    names: string[]
  ) => {
    if (!result) {
      return undefined;
    }

    return result.kpis.find(
      (kpi) =>
        names.includes(kpi.name)
    );
  };

  const totalProduction =
    getKPIByNames([
      "Tổng sản lượng",
    ]);

  const defectRate =
    getKPIByNames([
      "Tỷ lệ lỗi chung",
      "Tỷ lệ lỗi tổng thể",
      "Tỷ lệ lỗi",
    ]);

  const totalDowntime =
    getKPIByNames([
      "Tổng downtime",
      "Tổng thời gian dừng máy",
      "Tổng thời gian ngừng máy",
    ]);

  const productCount =
    getKPIByNames([
      "Số lượng sản phẩm",
      "Số sản phẩm",
    ]);

  // --------------------------------
  // Chart
  // --------------------------------

  const maxDailyProduction =
    useMemo(() => {
      if (
        !result ||
        result.daily_production.length === 0
      ) {
        return 0;
      }

      return Math.max(
        ...result.daily_production.map(
          (item) =>
            item.production
        )
      );
    }, [result]);

  const comparisonModels = result
    ? Array.from(
        new Set(
          result.model_performance
            .map((item) => item.model)
            .filter(
              (model): model is string =>
                Boolean(model && model !== "Không xác định")
            )
        )
      )
    : [];

  const comparisonMetrics = comparison
    ? [
        {
          key: "production",
          label: "Production",
          unit: "sản phẩm",
          valueA: comparison.models.model_a.production,
          valueB: comparison.models.model_b.production,
          difference: comparison.differences.production,
        },
        {
          key: "defects",
          label: "Defects",
          unit: "lỗi",
          valueA: comparison.models.model_a.defects,
          valueB: comparison.models.model_b.defects,
          difference: comparison.differences.defects,
        },
        {
          key: "defect_rate",
          label: "Defect Rate",
          unit: "%",
          valueA: comparison.models.model_a.defect_rate,
          valueB: comparison.models.model_b.defect_rate,
          difference:
            comparison.differences.defect_rate_percentage_points,
        },
        {
          key: "downtime",
          label: "Downtime",
          unit: "phút",
          valueA: comparison.models.model_a.downtime,
          valueB: comparison.models.model_b.downtime,
          difference: comparison.differences.downtime,
        },
      ]
    : [];

  const formatComparisonValue = (
    value: number | null,
    unit: string
  ) => {
    if (value === null) {
      return "Không đủ dữ liệu";
    }

    const formatted = value.toLocaleString("vi-VN", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });
    return `${formatted} ${unit}`;
  };

  return (
    <main className="dashboard-app">

      <div className="background-glow background-glow-one" />
      <div className="background-glow background-glow-two" />

      <div className="dashboard-container">

        {/* -------------------------------- */}
        {/* Header */}
        {/* -------------------------------- */}

        <header className="dashboard-topbar">

          <div className="dashboard-brand">

            <div className="brand-mark">
              HW
            </div>

            <div>
              <h1>HardwareAI</h1>

              <span>
                Hardware Intelligence
              </span>
            </div>

          </div>

          <div className="system-status">

            <span className="status-dot" />

            Gemini AI

          </div>

        </header>



        {/* -------------------------------- */}
        {/* Hero */}
        {/* -------------------------------- */}

        <section className="dashboard-hero">

          <div className="hero-badge">
            AI Hardware Analytics
          </div>

          <h2>
            Understand your hardware
            <br />
            with{" "}
            <span>
              data-driven insights.
            </span>
          </h2>

          <p>
            Upload dữ liệu sản xuất phần cứng
            từ CSV hoặc Excel. HardwareAI sẽ
            tính toán KPI, phân tích sản phẩm,
            model, linh kiện, nhà cung cấp,
            lô sản xuất và tạo các nhận định
            bằng AI.
          </p>

        </section>



        {/* -------------------------------- */}
        {/* Upload */}
        {/* -------------------------------- */}

        <section className="upload-card">

          <div className="section-heading">

            <div>

              <span className="section-number">
                01
              </span>

              <div>

                <h3>
                  Hardware data
                </h3>

                <p>
                  Upload dữ liệu phần cứng
                  để bắt đầu phân tích.
                </p>

              </div>

            </div>

            {selectedFile && (
              <button
                type="button"
                className="clear-button"
                onClick={handleClear}
                disabled={loading || comparisonLoading}
              >
                Clear
              </button>
            )}

          </div>



          <form
            onSubmit={handleSubmit}
          >

            <label
              htmlFor="hardware-file"
              className="production-upload"
            >

              <div className="upload-plus">
                +
              </div>

              <div>

                <strong>
                  Upload hardware data
                </strong>

                <span>
                  CSV hoặc XLSX · Max 10MB
                </span>

              </div>

            </label>

            <input
              id="hardware-file"
              type="file"
              accept=".csv,.xlsx"
              onChange={
                handleFileChange
              }
              disabled={loading || comparisonLoading}
              hidden
            />



            {selectedFile && (
              <div className="selected-production-file">

                <div>

                  <strong>
                    {selectedFile.name}
                  </strong>

                  <span>
                    {(
                      selectedFile.size /
                      1024
                    ).toFixed(1)} KB
                  </span>

                </div>

                <button
                  type="button"
                  onClick={
                    handleClear
                  }
                  disabled={loading || comparisonLoading}
                >
                  Remove
                </button>

              </div>
            )}



            {error && (
              <div className="error-box">

                <strong>
                  Error
                </strong>

                <span>
                  {error}
                </span>

              </div>
            )}



            <div className="upload-footer">

              <div className="upload-hint">

                <span>
                  AI
                </span>

                KPI · Quality · Components · Suppliers · Insights

              </div>

              <button
                type="submit"
                className="analyze-button"
                disabled={loading || comparisonLoading}
              >

                {loading ? (
                  <>
                    <span className="spinner" />

                    Analyzing...
                  </>
                ) : (
                  <>
                    Analyze hardware

                    <span>
                      →
                    </span>
                  </>
                )}

              </button>

            </div>

          </form>

        </section>



        {/* -------------------------------- */}
        {/* Loading */}
        {/* -------------------------------- */}

        {loading && (
          <section className="dashboard-loading">

            <div className="loading-animation">

              <span />
              <span />
              <span />

            </div>

            <h3>
              HardwareAI is analyzing hardware data
            </h3>

            <p>
              Calculating KPIs and generating
              hardware intelligence...
            </p>

          </section>
        )}



        {/* -------------------------------- */}
        {/* Dashboard */}
        {/* -------------------------------- */}

        {result && !loading && (
          <section
            id="dashboard"
            className="production-dashboard"
          >

            {/* Dashboard heading */}

            <div className="dashboard-heading">

              <div>

                <div className="result-label">
                  HARDWARE ANALYSIS
                </div>

                <h2>
                  <AIText>{result.title}</AIText>
                </h2>

                <p>
                  <AIText>{result.summary}</AIText>
                </p>

              </div>

              <div className="data-file">

                <span>
                  DATASET
                </span>

                <strong>
                  {selectedFile?.name}
                </strong>

              </div>

            </div>

            <section className="dashboard-card model-comparison-card">
              <div className="dashboard-card-header">
                <div>
                  <span>02</span>
                  <h3>So sánh hai model</h3>
                </div>
                <p>Compare model KPIs</p>
              </div>

              {comparisonModels.length < 2 ? (
                <p className="comparison-empty">
                  {!comparisonModels.length
                    ? "Không tìm thấy dữ liệu model hợp lệ trong dataset."
                    : "Cần ít nhất hai model có dữ liệu hợp lệ để so sánh."}
                </p>
              ) : (
                <>
                  <div className="comparison-controls">
                    <label>
                      <span>Model A</span>
                      <select
                        value={selectedModelA}
                        onChange={(event) =>
                          setSelectedModelA(event.target.value)
                        }
                      >
                        {comparisonModels.map((model) => (
                          <option key={model} value={model}>
                            {model}
                          </option>
                        ))}
                      </select>
                    </label>

                    <span className="comparison-versus">VS</span>

                    <label>
                      <span>Model B</span>
                      <select
                        value={selectedModelB}
                        onChange={(event) =>
                          setSelectedModelB(event.target.value)
                        }
                      >
                        {comparisonModels.map((model) => (
                          <option key={model} value={model}>
                            {model}
                          </option>
                        ))}
                      </select>
                    </label>

                    <button
                      type="button"
                      className="comparison-button"
                      onClick={handleCompare}
                      disabled={
                        comparisonLoading ||
                        !selectedModelA ||
                        !selectedModelB ||
                        selectedModelA === selectedModelB
                      }
                    >
                      {comparisonLoading
                        ? "Đang so sánh..."
                        : "So sánh model"}
                    </button>
                  </div>

                  {selectedModelA === selectedModelB && (
                    <p className="comparison-hint">
                      Chọn hai model khác nhau để tiếp tục.
                    </p>
                  )}

                  {comparisonError && (
                    <div className="error-box comparison-error">
                      <strong>Không thể so sánh</strong>
                      <span>{comparisonError}</span>
                    </div>
                  )}

                  {comparisonLoading && (
                    <div
                      className="comparison-loading"
                      role="status"
                      aria-live="polite"
                    >
                      <span className="spinner" />
                      Đang tính KPI và tạo nhận xét AI...
                    </div>
                  )}

                  {comparison && !comparisonLoading && (
                    <>
                      <div className="comparison-conclusion">
                        <strong>
                          {comparison.rate_comparison_status ===
                          "insufficient_production"
                            ? "Chưa thể so sánh tỷ lệ lỗi"
                            : comparison.higher_defect_rate_model
                              ? `${comparison.higher_defect_rate_model} có tỷ lệ lỗi cao hơn`
                              : "Tỷ lệ lỗi của hai model bằng nhau"}
                        </strong>
                        <p>
                          {comparison.differences
                            .defect_rate_percentage_points === null
                            ? "Production bằng 0 ở ít nhất một model nên không thể xác định tỷ lệ lỗi."
                            : `Chênh lệch tỷ lệ lỗi: ${Math.abs(
                                comparison.differences
                                  .defect_rate_percentage_points
                              ).toLocaleString("vi-VN", {
                                minimumFractionDigits: 2,
                                maximumFractionDigits: 2,
                              })} điểm phần trăm (B − A).`}
                        </p>
                      </div>

                      <div className="comparison-chart">
                        {comparisonMetrics.map((metric) => {
                          const values = [
                            metric.valueA || 0,
                            metric.valueB || 0,
                          ];
                          const maxValue = Math.max(...values, 1);

                          return (
                            <div
                              className="comparison-chart-metric"
                              key={metric.key}
                            >
                              <div className="comparison-chart-title">
                                <strong>{metric.label}</strong>
                                <span>
                                  Δ{" "}
                                  {formatComparisonValue(
                                    metric.difference,
                                    metric.key === "defect_rate"
                                      ? "điểm %"
                                      : metric.unit
                                  )}
                                </span>
                              </div>
                              {[0, 1].map((index) => {
                                const model =
                                  index === 0
                                    ? comparison.models.model_a
                                    : comparison.models.model_b;
                                const value =
                                  index === 0
                                    ? metric.valueA
                                    : metric.valueB;
                                const numericValue = value || 0;

                                return (
                                  <div
                                    className="comparison-bar-row"
                                    key={model.name}
                                  >
                                    <span>{model.name}</span>
                                    <div className="comparison-bar-track">
                                      <div
                                        className={
                                          index === 0
                                            ? "comparison-bar comparison-bar-a"
                                            : "comparison-bar comparison-bar-b"
                                        }
                                        style={{
                                          width: `${
                                            (numericValue / maxValue) * 100
                                          }%`,
                                        }}
                                      />
                                    </div>
                                    <strong>
                                      {formatComparisonValue(
                                        value,
                                        metric.unit
                                      )}
                                    </strong>
                                  </div>
                                );
                              })}
                            </div>
                          );
                        })}
                      </div>

                      {comparison.ai_analysis ? (
                        <div className="comparison-ai">
                          <span>AI INSIGHT</span>
                          <p>{comparison.ai_analysis.summary}</p>
                          <ul>
                            {comparison.ai_analysis.investigation_points.map(
                              (point, index) => (
                                <li key={`${index}-${point}`}>{point}</li>
                              )
                            )}
                          </ul>
                        </div>
                      ) : (
                        <div className="comparison-ai comparison-ai-unavailable">
                          <span>AI INSIGHT</span>
                          <p>
                            {comparison.ai_error ||
                              "Nhận xét AI hiện không khả dụng."}
                          </p>
                        </div>
                      )}
                    </>
                  )}
                </>
              )}
            </section>


            {/* -------------------------------- */}
            {/* KPI Cards */}
            {/* -------------------------------- */}

            <section className="kpi-grid">

              <KPICard
                label="Tổng sản lượng"
                value={
                  totalProduction
                    ? Number(
                        totalProduction.value
                      ).toLocaleString(
                        "vi-VN"
                      )
                    : "--"
                }
                unit={
                  totalProduction?.unit ||
                  "đơn vị"
                }
                description={totalProduction?.description}
              />

              <KPICard
                label="Tỷ lệ lỗi"
                value={
                  defectRate
                    ? Number(
                        defectRate.value
                      ).toLocaleString("vi-VN", {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })
                    : "--"
                }
                unit={
                  defectRate?.unit ||
                  "%"
                }
                description={defectRate?.description}
              />

              <KPICard
                label="Tổng downtime"
                value={
                  totalDowntime
                    ? Number(
                        totalDowntime.value
                      ).toLocaleString(
                        "vi-VN"
                      )
                    : "--"
                }
                unit={
                  totalDowntime?.unit ||
                  "phút"
                }
                description={totalDowntime?.description}
              />

              <KPICard
                label="Số sản phẩm"
                value={
                  productCount
                    ? Number(
                        productCount.value
                      ).toString()
                    : "--"
                }
                unit={
                  productCount?.unit ||
                  "sản phẩm"
                }
              />

            </section>



            {/* -------------------------------- */}
            {/* Data Quality */}
            {/* -------------------------------- */}

            <DataQualityCard
              dataQuality={
                result.data_quality
              }
              dataProcessing={result.data_processing}
              hasDefectTypeData={
                result.data_scope.has_defect_type_data
              }
            />



            {/* -------------------------------- */}
            {/* Daily production */}
            {/* -------------------------------- */}

            {result.daily_production.length >
              0 && (
              <section className="dashboard-card">

                <div className="dashboard-card-header">

                  <div>

                    <span>
                      03
                    </span>

                    <h3>
                      Sản lượng theo ngày
                    </h3>

                  </div>

                  <p>
                    Production output
                  </p>

                </div>



                <div className="daily-chart">

                  {result.daily_production.map(
                    (item) => {

                      const height =
                        maxDailyProduction >
                        0
                          ? Math.max(
                              8,
                              (
                                item.production /
                                maxDailyProduction
                              ) * 100
                            )
                          : 0;

                      return (
                        <div
                          className="chart-column"
                          key={item.date}
                        >

                          <div className="chart-value">
                            {item.production.toLocaleString(
                              "vi-VN"
                            )}
                          </div>

                          <div className="chart-bar-wrapper">

                            <div
                              className="chart-bar"
                              style={{
                                height:
                                  `${height}%`,
                              }}
                            />

                          </div>

                          <div className="chart-date">
                            {formatDate(
                              item.date
                            )}
                          </div>

                        </div>
                      );
                    }
                  )}

                </div>

              </section>
            )}



            {/* -------------------------------- */}
            {/* Product */}
            {/* -------------------------------- */}

            {result.product_performance.length >
              0 && (
              <PerformanceTable
                number="04"
                title="Phân tích theo sản phẩm"
                subtitle="Product performance"
                items={
                  result.product_performance
                }
                field="product"
              />
            )}



            {/* -------------------------------- */}
            {/* Model */}
            {/* -------------------------------- */}

            {result.model_performance.length >
              0 && (
              <PerformanceTable
                number="05"
                title="Phân tích theo model"
                subtitle="Model performance"
                items={
                  result.model_performance
                }
                field="model"
              />
            )}



            {/* -------------------------------- */}
            {/* Component */}
            {/* -------------------------------- */}

            {result.component_performance.length >
              0 && (
              <PerformanceTable
                number="06"
                title="Phân tích theo linh kiện"
                subtitle="Component performance"
                items={
                  result.component_performance
                }
                field="component"
              />
            )}



            {/* -------------------------------- */}
            {/* Supplier */}
            {/* -------------------------------- */}

            {result.supplier_performance.length >
              0 && (
              <PerformanceTable
                number="07"
                title="Phân tích theo nhà cung cấp"
                subtitle="Supplier performance"
                items={
                  result.supplier_performance
                }
                field="supplier"
              />
            )}



            {/* -------------------------------- */}
            {/* Batch */}
            {/* -------------------------------- */}

            {result.batch_performance.length >
              0 && (
              <PerformanceTable
                number="08"
                title="Phân tích theo lô sản xuất"
                subtitle="Batch performance"
                items={
                  result.batch_performance
                }
                field="batch"
              />
            )}



            {/* -------------------------------- */}
            {/* Defect types */}
            {/* -------------------------------- */}

            {result.defect_type_analysis.length >
              0 && (
              <section className="dashboard-card">

                <div className="dashboard-card-header">

                  <div>

                    <span>
                      09
                    </span>

                    <h3>
                      Phân tích loại lỗi
                    </h3>

                  </div>

                  <p>
                    Defect type analysis
                  </p>

                </div>



                <div className="machine-table-wrapper">

                  <table className="machine-table">

                    <thead>

                      <tr>

                        <th>
                          Defect type
                        </th>

                        <th>
                          Defects
                        </th>

                      </tr>

                    </thead>

                    <tbody>

                      {result.defect_type_analysis.map(
                        (item) => (
                          <tr
                            key={
                              item.defect_type
                            }
                          >

                            <td>
                              <strong>
                                {
                                  item.defect_type
                                }
                              </strong>
                            </td>

                            <td>
                              {item.defects.toLocaleString(
                                "vi-VN"
                              )}
                            </td>

                          </tr>
                        )
                      )}

                    </tbody>

                  </table>

                </div>

              </section>
            )}



            {/* -------------------------------- */}
            {/* AI Insights */}
            {/* -------------------------------- */}

            <section className="ai-grid">

              <AISection
                number="10"
                title="AI Insights"
                items={
                  result.insights
                }
                className="insights"
              />

              <AISection
                number="11"
                title="Warnings"
                items={
                  result.warnings
                }
                className="warnings"
              />

            </section>



            {/* -------------------------------- */}
            {/* Recommendations */}
            {/* -------------------------------- */}

            <AISection
              number="12"
              title="AI Recommendations"
              items={
                result.recommendations
              }
              className="recommendations"
            />

            {/* -------------------------------- */}
            {/* AI Deep Analysis */}
            {/* -------------------------------- */}

            <section className="ai-deep-analysis">

              <div className="ai-deep-header">
                <div>
                  <div className="result-label">
                    AI DEEP ANALYSIS
                  </div>

                  <h2>
                    Phân tích sản xuất chuyên sâu
                  </h2>

                  <p>
                    Phân tích chuyên sâu dựa trên KPI, chất lượng dữ liệu
                    và các chỉ số vận hành đã được hệ thống tính toán.
                  </p>
                </div>

                <div className="ai-deep-badge">
                  <span className="ai-deep-status-dot" />
                  Gemini AI
                </div>
              </div>

              <AIAnalysisSection
                number="13"
                title="Tóm tắt điều hành"
                subtitle="Executive summary"
                analysis={result.ai_analysis.executive_summary}
              />

              <AIAnalysisSection
                number="14"
                title="Phân tích sản xuất"
                subtitle="Production analysis"
                analysis={result.ai_analysis.production_analysis}
              />

              <AIAnalysisSection
                number="15"
                title="Phân tích chất lượng"
                subtitle="Quality analysis"
                analysis={result.ai_analysis.quality_analysis}
              />

              <AIAnalysisSection
                number="16"
                title="Phân tích linh kiện"
                subtitle="Component analysis"
                analysis={result.ai_analysis.component_analysis}
              />

              <AIAnalysisSection
                number="17"
                title="Phân tích nhà cung cấp"
                subtitle="Supplier analysis"
                analysis={result.ai_analysis.supplier_analysis}
              />

              <AIAnalysisSection
                number="18"
                title="Phân tích lô sản xuất"
                subtitle="Batch analysis"
                analysis={result.ai_analysis.batch_analysis}
              />

              <AIAnalysisSection
                number="19"
                title="Phân tích downtime"
                subtitle="Downtime analysis"
                analysis={result.ai_analysis.downtime_analysis}
              />

              <AnomalyHighlightsSection
                number="20"
                anomalyAnalysis={result.anomaly_analysis}
              />

              <RiskAreasSection
                number="21"
                risks={result.ai_analysis.risk_areas}
              />

              <RecommendedActionsSection
                number="22"
                actions={result.ai_analysis.recommended_actions}
              />

            </section>



            {/* -------------------------------- */}
            {/* Footer */}
            {/* -------------------------------- */}

            <div className="dashboard-footer">

              <span>
                Generated by HardwareAI
              </span>

              <button
                type="button"
                onClick={() =>
                  window.scrollTo({
                    top: 0,
                    behavior: "smooth",
                  })
                }
              >
                Analyze another dataset ↑
              </button>

            </div>

          </section>
        )}

      </div>

    </main>
  );
}



/* -------------------------------- */
/* KPI Card */
/* -------------------------------- */

function KPICard({
  label,
  value,
  unit,
  description,
}: {
  label: string;
  value: string;
  unit: string;
  description?: string;
}) {
  return (
    <div className="kpi-card">

      <span className="kpi-label">
        {label}
      </span>

      <div className="kpi-value">
        {value}
      </div>

      <span className="kpi-unit">
        {unit}
      </span>

      {description && (
        <p className="kpi-description">
          {description}
        </p>
      )}

    </div>
  );
}



/* -------------------------------- */
/* Data Quality Card */
/* -------------------------------- */

function DataQualityCard({
  dataQuality,
  dataProcessing,
  hasDefectTypeData,
}: {
  dataQuality: DataQuality;
  dataProcessing: DataProcessing;
  hasDefectTypeData: boolean;
}) {
  const score =
    Number(
      dataQuality?.quality_score ?? 0
    );

  const qualityStatuses = {
    good: {
      label: "Tốt",
      className: "quality-good",
    },
    warning: {
      label: "Cần lưu ý",
      className: "quality-review",
    },
    poor: {
      label: "Kém",
      className: "quality-poor",
    },
  };

  const qualityStatus = qualityStatuses[dataQuality.quality_status];

  const totalIssues =
    dataQuality.invalid_rows +
    dataQuality.empty_rows;

  const hasProblems =
    totalIssues > 0 ||
    Object.keys(
      dataQuality.missing_values || {}
    ).length > 0 ||
    Object.keys(
      dataQuality.invalid_values || {}
    ).length > 0 ||
    Object.keys(
      dataQuality.negative_values || {}
    ).length > 0 ||
    (
      dataQuality.missing_columns &&
      dataQuality.missing_columns.length > 0
    );

  const issueMessages: string[] = [];

  Object.entries(
    dataQuality.missing_values || {}
  ).forEach(
    ([field, count]) => {
      issueMessages.push(
        `Thiếu ${count} giá trị ở trường '${field}'.`
      );
    }
  );

  Object.entries(
    dataQuality.invalid_values || {}
  ).forEach(
    ([field, count]) => {
      issueMessages.push(
        `Có ${count} giá trị không hợp lệ ở trường '${field}'.`
      );
    }
  );

  Object.entries(
    dataQuality.negative_values || {}
  ).forEach(
    ([field, count]) => {
      issueMessages.push(
        `Có ${count} giá trị âm ở trường '${field}'.`
      );
    }
  );

  if (dataQuality.empty_rows > 0) {
    issueMessages.push(
      `Có ${dataQuality.empty_rows} dòng trống.`
    );
  }

  if (dataQuality.duplicate_rows > 0) {
    issueMessages.push(
      `Có ${dataQuality.duplicate_rows} dòng trùng lặp; các lần xuất hiện trùng sau bản ghi đầu đã được tính trong nhóm dòng không hợp lệ.`
    );
  }

  if (
    dataQuality.missing_columns &&
    dataQuality.missing_columns.length > 0
  ) {
    issueMessages.push(
      `Thiếu cột: ${dataQuality.missing_columns.join(
        ", "
      )}.`
    );
  }

  return (
    <section
      className={`dashboard-card data-quality-card ${qualityStatus.className}`}
    >

      <div className="dashboard-card-header">

        <div>

          <span>
            02
          </span>

          <h3>
            Data Quality
          </h3>

        </div>

        <p>
          Dataset validation
        </p>

      </div>



      <div className="data-quality-main">

        <div className="quality-score">

          <div className="quality-score-number">
            {score.toLocaleString("vi-VN", {
              minimumFractionDigits: 1,
              maximumFractionDigits: 1,
            })}
          </div>

          <div className="quality-score-total">
            / 100
          </div>

        </div>

        <div className="quality-status">

          <strong>
            {qualityStatus.label}
          </strong>

          <span>
            {hasProblems
              ? "Dựa trên quy tắc kiểm tra chất lượng dữ liệu đầu vào"
              : "Không phát hiện vấn đề theo các quy tắc kiểm tra"}
          </span>

        </div>

      </div>



      <div className="quality-stats">

        <QualityStat
          label="Tổng dòng ban đầu"
          value={dataQuality.total_rows}
        />

        <QualityStat
          label="Đánh giá hợp lệ"
          value={dataQuality.valid_rows}
        />

        <QualityStat
          label="Đánh giá không hợp lệ"
          value={dataQuality.invalid_rows}
        />

        <QualityStat
          label="Dòng trùng (nằm trong không hợp lệ)"
          value={dataQuality.duplicate_rows}
        />

        <QualityStat
          label="Cảnh báo dữ liệu"
          value={dataQuality.warnings?.length ?? 0}
        />

      </div>

      <div className="processing-summary">
        <div className="processing-summary-heading">
          <div>
            <strong>Lọc bản ghi để tính KPI</strong>
            <span>
              Quy trình này độc lập với đánh giá chất lượng dữ liệu ở trên.
            </span>
          </div>
          <span className="processing-kpi-count">
            {dataProcessing.rows_used_for_kpi.toLocaleString("vi-VN")} dùng tính KPI
          </span>
        </div>

        <div className="processing-stats">
          <QualityStat
            label="Sau loại dòng trống/trùng"
            value={dataProcessing.rows_after_deduplication}
          />
          <QualityStat
            label="Được dùng tính KPI"
            value={dataProcessing.rows_used_for_kpi}
          />
          <QualityStat
            label="Bị loại khỏi KPI"
            value={dataProcessing.invalid_metric_rows_excluded}
          />
        </div>

        {dataProcessing.invalid_metric_rows_excluded > 0 && (
          <div className="processing-reasons">
            <span>Lý do theo metric</span>
            <div>
              {Object.entries(
                dataProcessing.invalid_metric_rows_excluded_by_metric || {}
              )
                .filter(([, count]) => count > 0)
                .map(([metric, count]) => (
                  <span className="processing-reason" key={metric}>
                    {metric}: {count.toLocaleString("vi-VN")} dòng
                  </span>
                ))}
            </div>
            <small>
              Một dòng có thể xuất hiện ở nhiều metric nếu có nhiều giá trị không hợp lệ.
            </small>
          </div>
        )}

        <p className="quality-scoring-basis">
          Điểm chất lượng = số dòng hợp lệ / số dòng dữ liệu × 100.
          Dòng trống được loại khỏi mẫu chấm điểm; các lần xuất hiện trùng
          sau bản ghi đầu được tính vào số dòng không hợp lệ.
        </p>

        <p className="quality-optional-field-note">
          {hasDefectTypeData
            ? 'Defect_Type là trường tùy chọn, không ảnh hưởng đến số dòng hợp lệ. Giá trị loại lỗi bị thiếu được gom vào nhóm "Không xác định" trong phân tích loại lỗi.'
            : "Dữ liệu không có cột Defect_Type; trường này không thuộc các điều kiện chấm điểm chất lượng dữ liệu."}
        </p>
        </div>


      {hasProblems && (
        <div className="quality-issues">

          <div className="quality-issues-title">
            Issues detected
          </div>

          <div className="quality-issues-list">

            {issueMessages
              .slice(0, 10)
              .map(
                (message, index) => (
                  <div
                    className="quality-issue"
                    key={`${message}-${index}`}
                  >

                    <span>
                      {String(
                        index + 1
                      ).padStart(2, "0")}
                    </span>

                    <p>
                      {message}
                    </p>

                  </div>
                )
              )}

          </div>

        </div>
      )}

      {!hasProblems && (
        <div className="quality-success">

          <strong>
            Dữ liệu không có lỗi theo các quy tắc đánh giá chất lượng.
          </strong>

          <span>
            {dataQuality.valid_rows} / {dataQuality.data_rows} dòng dữ liệu
            được đánh giá hợp lệ; {dataProcessing.rows_used_for_kpi} dòng
            được sử dụng để tính KPI.
          </span>

        </div>
      )}

    </section>
  );
}



/* -------------------------------- */
/* Quality Stat */
/* -------------------------------- */

function QualityStat({
  label,
  value,
}: {
  label: string;
  value: number;
}) {
  return (
    <div className="quality-stat">

      <span>
        {label}
      </span>

      <strong>
        {value.toLocaleString(
          "vi-VN"
        )}
      </strong>

    </div>
  );
}



/* -------------------------------- */
/* Performance Table */
/* -------------------------------- */

function PerformanceTable({
  number,
  title,
  subtitle,
  items,
  field,
}: {
  number: string;
  title: string;
  subtitle: string;
  items: PerformanceItem[];
  field:
    | "product"
    | "model"
    | "component"
    | "supplier"
    | "batch";
}) {
  return (
    <section className="dashboard-card">

      <div className="dashboard-card-header">

        <div>

          <span>
            {number}
          </span>

          <h3>
            {title}
          </h3>

        </div>

        <p>
          {subtitle}
        </p>

      </div>



      <div className="machine-table-wrapper">

        <table className="machine-table">

          <thead>

            <tr>

              <th>
                {getFieldLabel(field)}
              </th>

              <th>
                Production
              </th>

              <th>
                Defects
              </th>

              <th>
                Defect rate
              </th>

              <th>
                Downtime
              </th>

            </tr>

          </thead>

          <tbody>

            {items.map(
              (item, index) => (

                <tr
                  key={
                    String(
                      item[field] ??
                      index
                    )
                  }
                >

                  <td>

                    <strong>
                      {String(
                        item[field] ??
                        "N/A"
                      )}
                    </strong>

                  </td>

                  <td>
                    {item.production.toLocaleString(
                      "vi-VN"
                    )}
                  </td>

                  <td>
                    {item.defects.toLocaleString(
                      "vi-VN"
                    )}
                  </td>

                  <td>

                    <span className="rate-value">
                      {item.defect_rate.toFixed(
                        2
                      )}
                      %
                    </span>

                  </td>

                  <td>
                    {item.downtime.toLocaleString(
                      "vi-VN"
                    )}{" "}
                    phút
                  </td>

                </tr>

              )
            )}

          </tbody>

        </table>

      </div>

    </section>
  );
}



/* -------------------------------- */
/* AI Section */
/* -------------------------------- */

function AISection({
  number,
  title,
  items,
  className,
}: {
  number: string;
  title: string;
  items: string[];
  className: string;
}) {
  return (
    <section
      className={`dashboard-card ai-card ${className}`}
    >

      <div className="dashboard-card-header">

        <div>

          <span>
            {number}
          </span>

          <h3>
            {title}
          </h3>

        </div>

      </div>



      {items.length === 0 ? (
        <p className="empty-ai">
          Không có dữ liệu.
        </p>
      ) : (
        <div className="ai-list">

          {items.map(
            (item, index) => (

              <div
                className="ai-item"
                key={index}
              >

                <span>
                  {String(
                    index + 1
                  ).padStart(2, "0")}
                </span>

                <p>
                  <AIText>{item}</AIText>
                </p>

              </div>

            )
          )}

        </div>
      )}

    </section>
  );
}



function AIAnalysisSection({
  number,
  title,
  subtitle,
  analysis,
}: {
  number: string;
  title: string;
  subtitle: string;
  analysis: AnalysisSection;
}) {
  return (
    <section className="dashboard-card ai-analysis-card">

      <div className="dashboard-card-header">
        <div>
          <div className="ai-section-title">
            <span>{number}</span>

            <h3>{title}</h3>
          </div>
        </div>

        <p>{subtitle}</p>
      </div>

      <div className="ai-analysis-summary">
        <p>
          <AIText>{analysis.summary}</AIText>
        </p>
      </div>

      {analysis.key_observations.length > 0 && (
        <div className="ai-observations">

          <div className="ai-subsection-title">
            Nhận định chính
          </div>

          <div className="ai-observation-list">

            {analysis.key_observations.map(
              (observation, index) => (
                <div
                  className="ai-observation"
                  key={`${number}-${index}`}
                >
                  <span>
                    {String(index + 1).padStart(2, "0")}
                  </span>

                  <p>
                    <AIText>{observation}</AIText>
                  </p>
                </div>
              )
            )}

          </div>
        </div>
      )}

    </section>
  );
}



function AnomalyHighlightsSection({
  number,
  anomalyAnalysis,
}: {
  number: string;
  anomalyAnalysis: AnomalyAnalysis;
}) {
  const findings = anomalyAnalysis.findings;
  const groupLabels: Record<string, string> = {
    product: "Sản phẩm",
    model: "Model",
    component: "Linh kiện",
    supplier: "Nhà cung cấp",
    batch: "Batch",
  };
  const highlights: {
    key: string;
    title: string;
    description: string;
    type: "ranking" | "rule";
  }[] = [];

  const addGroupFindings = (
    source: Record<string, GroupExtreme> | undefined,
    metric: "defect_rate" | "downtime" | "production"
  ) => {
    Object.entries(source ?? {}).forEach(([group, finding]) => {
      const groupLabel = groupLabels[group] ?? group;
      const value = finding.value.toLocaleString("vi-VN", {
        maximumFractionDigits: 2,
      });
      const title =
        metric === "defect_rate"
          ? `Tỷ lệ lỗi cao nhất · ${groupLabel}`
          : metric === "downtime"
            ? `Downtime cao nhất · ${groupLabel}`
            : `Sản lượng thấp nhất · ${groupLabel}`;
      const description =
        metric === "defect_rate"
          ? `${finding.entity}: ${value}%. Đây là thứ hạng trong dữ liệu, không tự chứng minh nguyên nhân gây lỗi.`
          : metric === "downtime"
            ? `${finding.entity}: ${value} phút. Đây là thứ hạng trong dữ liệu, không tự xác định nguyên nhân downtime.`
            : `${finding.entity}: ${value} đơn vị. Đây là thứ hạng trong dữ liệu.`;

      highlights.push({
        key: `${metric}-${group}-${finding.entity}`,
        title,
        description,
        type: "ranking",
      });
    });
  };

  addGroupFindings(findings.highest_defect_rate, "defect_rate");
  addGroupFindings(findings.highest_downtime, "downtime");
  addGroupFindings(findings.lowest_production, "production");

  const dailyChange = findings.largest_daily_change;
  if (
    dailyChange?.detected &&
    dailyChange.from_date &&
    dailyChange.to_date &&
    dailyChange.previous_production !== undefined &&
    dailyChange.current_production !== undefined
  ) {
    const directionLabel = {
      increase: "tăng",
      decrease: "giảm",
      unchanged: "không đổi",
    }[dailyChange.direction ?? "unchanged"];
    const changeAmount =
      dailyChange.change === undefined
        ? ""
        : ` (${dailyChange.change > 0 ? "+" : ""}${dailyChange.change.toLocaleString("vi-VN")} đơn vị)`;
    highlights.push({
      key: "largest-daily-change",
      title: "Thay đổi sản lượng lớn nhất giữa hai ngày liên tiếp",
      description:
        `Từ ${dailyChange.from_date} đến ${dailyChange.to_date}, sản lượng ${directionLabel} từ ${dailyChange.previous_production.toLocaleString("vi-VN")} đến ${dailyChange.current_production.toLocaleString("vi-VN")} đơn vị${changeAmount}.`,
      type: "ranking",
    });
  }

  const batchFinding = findings.unusual_batch_defect_rate;
  if (batchFinding?.detected) {
    batchFinding.outliers.forEach((outlier) => {
      highlights.push({
        key: `batch-outlier-${outlier.entity}`,
        title: "Batch ngoài ngưỡng IQR",
        description:
          `${outlier.entity}: tỷ lệ lỗi ${outlier.defect_rate.toLocaleString("vi-VN", { maximumFractionDigits: 2 })}%. Đây là phát hiện thống kê, không phải kết luận nguyên nhân.`,
        type: "rule",
      });
    });
  }

  const modelFinding = findings.model_quality_difference;
  if (
    modelFinding?.detected &&
    modelFinding.lowest_model &&
    modelFinding.highest_model &&
    modelFinding.spread_percentage_points !== undefined
  ) {
    highlights.push({
      key: "model-quality-difference",
      title: "Chênh lệch tỷ lệ lỗi giữa các model vượt ngưỡng",
      description:
        `${modelFinding.lowest_model} đến ${modelFinding.highest_model}: chênh ${modelFinding.spread_percentage_points.toLocaleString("vi-VN", { maximumFractionDigits: 2 })} điểm phần trăm.`,
      type: "rule",
    });
  }

  const ruleNotes = [
    ...(dailyChange?.detected === false && dailyChange.reason
      ? [`Biến động theo ngày: ${dailyChange.reason}`]
      : []),
    ...(batchFinding?.detected === false && batchFinding.reason
      ? [`Ngoại lệ batch (IQR): ${batchFinding.reason}`]
      : []),
    ...(modelFinding?.detected === false && modelFinding.reason
      ? [`Chênh lệch model: ${modelFinding.reason}`]
      : []),
  ];

  return (
    <section className="dashboard-card anomaly-highlights-card">
      <div className="dashboard-card-header">
        <div>
          <div className="ai-section-title">
            <span>{number}</span>
            <h3>Phát hiện và thứ hạng đáng chú ý</h3>
          </div>
        </div>
        <p>Python anomaly analysis</p>
      </div>

      <p className="anomaly-guidance">
        Các phát hiện dưới đây được đưa vào để AI cân nhắc khi lập khuyến nghị.
        Không phải mỗi phát hiện đều cần một hành động riêng; thứ hạng cao nhất
        không đồng nghĩa với ngoại lệ thống kê hay nguyên nhân đã được xác nhận.
      </p>

      {anomalyAnalysis.status !== "completed" ? (
        <div className="empty-ai">
          Chưa đủ dữ liệu để hoàn tất phân tích bất thường.
        </div>
      ) : (
        <>
          {highlights.length > 0 ? (
            <div className="anomaly-highlight-list">
              {highlights.map((highlight) => (
                <article
                  className={`anomaly-highlight anomaly-${highlight.type}`}
                  key={highlight.key}
                >
                  <span className="anomaly-highlight-type">
                    {highlight.type === "rule"
                      ? "Phát hiện theo quy tắc"
                      : "Thứ hạng"}
                  </span>
                  <h4>{highlight.title}</h4>
                  <p>{highlight.description}</p>
                </article>
              ))}
            </div>
          ) : (
            <div className="empty-ai">
              Không có thứ hạng hoặc phát hiện bất thường đủ dữ liệu để hiển thị.
            </div>
          )}

          {ruleNotes.length > 0 && (
            <div className="anomaly-rule-notes">
              {ruleNotes.map((note) => (
                <p key={note}>{note}</p>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}



function RiskAreasSection({
  number,
  risks,
}: {
  number: string;
  risks: RiskArea[];
}) {
  return (
    <section className="dashboard-card risk-areas-card">

      <div className="dashboard-card-header">

        <div>
          <div className="ai-section-title">
            <span>{number}</span>

            <h3>Khu vực rủi ro</h3>
          </div>
        </div>

        <p>Risk areas</p>

      </div>

      <p className="risk-level-note">
        Backend chuẩn hóa mức rủi ro theo ngưỡng đã cấu hình. Nếu chưa có
        ngưỡng tương ứng, “Trung bình” là mức mặc định trung tính, không
        phải kết luận về mức độ nghiêm trọng.
      </p>

      {risks.length === 0 ? (
        <div className="empty-ai">
          Không phát hiện khu vực rủi ro nổi bật.
        </div>
      ) : (
        <div className="risk-grid">

          {risks.map((risk, index) => (
            <RiskAreaCard
              key={`${risk.entity_type}-${risk.entity_name}-${index}`}
              risk={risk}
            />
          ))}

        </div>
      )}

    </section>
  );
}



function RiskAreaCard({
  risk,
}: {
  risk: RiskArea;
}) {
  const riskLabel = {
    low: "Thấp",
    medium: "Trung bình",
    high: "Cao",
    critical: "Nghiêm trọng",
  }[risk.risk_level];

  const entityTypeLabel = {
    product: "Sản phẩm",
    model: "Model",
    component: "Linh kiện",
    supplier: "Nhà cung cấp",
    batch: "Batch",
    defect_type: "Loại lỗi",
    date: "Ngày",
    data_quality: "Chất lượng dữ liệu",
  }[risk.entity_type];

  return (
    <article className="risk-area-card">

      <div className="risk-area-top">
        <div className="risk-area-identity">
          <span className="risk-entity-type">
            {entityTypeLabel}
          </span>

          <h4>
            <AIText>{risk.entity_name}</AIText>
          </h4>
        </div>

        <span
          className={`risk-level risk-${risk.risk_level}`}
        >
          {riskLabel}
        </span>

      </div>

      <div className="risk-evidence">

        <div className="ai-subsection-title">
          Bằng chứng
        </div>

        <div className="risk-evidence-grid">

          {risk.evidence.production !== undefined && (
            <EvidenceItem
              label="Sản lượng"
              value={risk.evidence.production}
              unit="đơn vị"
            />
          )}

          {risk.evidence.defects !== undefined && (
            <EvidenceItem
              label="Số lỗi"
              value={risk.evidence.defects}
              unit="lỗi"
            />
          )}

          {risk.evidence.defect_rate !== undefined && (
            <EvidenceItem
              label="Tỷ lệ lỗi"
              value={risk.evidence.defect_rate}
              unit="%"
            />
          )}

          {risk.evidence.downtime !== undefined && (
            <EvidenceItem
              label="Downtime"
              value={risk.evidence.downtime}
              unit="phút"
            />
          )}

          {risk.evidence.quality_score !== undefined && (
            <EvidenceItem
              label="Điểm chất lượng dữ liệu"
              value={risk.evidence.quality_score}
              unit="/100"
            />
          )}

        </div>

      </div>

      <div className="risk-observation">

        <div className="ai-subsection-title">
          Nhận định
        </div>

        <p>
          <AIText>{risk.observation}</AIText>
        </p>

      </div>

      {risk.investigation_points.length > 0 && (
        <div className="risk-investigation">

          <div className="ai-subsection-title">
            Cần kiểm tra
          </div>

          <div className="investigation-list">

            {risk.investigation_points.map(
              (point, index) => (
                <div
                  className="investigation-item"
                  key={index}
                >
                  <span>→</span>

                  <p>
                    <AIText>{point}</AIText>
                  </p>
                </div>
              )
            )}

          </div>
        </div>
      )}

    </article>
  );
}



function EvidenceItem({
  label,
  value,
  unit,
}: {
  label: string;
  value: number;
  unit: string;
}) {
  const isNegative = value < 0;

  const formattedValue =
    label === "Tỷ lệ lỗi"
      ? formatAIValue(value, "percent")
      : label === "Điểm chất lượng dữ liệu"
        ? formatAIValue(value, "number")
      : formatAIValue(value, "number");

  return (
    <div
      className={`evidence-item ${
        isNegative ? "evidence-negative" : ""
      }`}
    >

      <span className="evidence-label">
        {label}
      </span>

      <div className="evidence-value-row">
        <strong>{formattedValue}</strong>

        <small>{unit}</small>
      </div>

      {isNegative && (
        <span className="evidence-warning">
          Giá trị bất thường
        </span>
      )}

    </div>
  );
}



function RecommendedActionsSection({
  number,
  actions,
}: {
  number: string;
  actions: RecommendedAction[];
}) {
  return (
    <section className="dashboard-card recommended-actions-card">

      <div className="dashboard-card-header">
        <div>
          <div className="ai-section-title">
            <span>{number}</span>

            <h3>Hành động đề xuất</h3>
          </div>
        </div>

        <p>Recommended actions</p>
      </div>

      <p className="risk-level-note">
        Backend xác định mức ưu tiên theo ngưỡng nghiệp vụ đã cấu hình;
        “Trung bình” là mức mặc định trung tính khi không có ngưỡng phù hợp.
      </p>

      {actions.length === 0 ? (
        <div className="empty-ai">
          Chưa có hành động đề xuất.
        </div>
      ) : (
        <div className="recommended-action-list">
          {actions.map((action, index) => (
            <RecommendedActionCard
              key={`${action.target_name}-${index}`}
              action={action}
              index={index}
            />
          ))}
        </div>
      )}

    </section>
  );
}



function RecommendedActionCard({
  action,
  index,
}: {
  action: RecommendedAction;
  index: number;
}) {
  const priorityLabel = {
    low: "Thấp",
    medium: "Trung bình",
    high: "Cao",
    critical: "Nghiêm trọng",
  }[action.priority];

  return (
    <article className="recommended-action">

      <div className="recommended-action-number">
        {String(index + 1).padStart(2, "0")}
      </div>

      <div className="recommended-action-content">

        <div className="recommended-action-heading">

          <span
            className={`priority-badge priority-${action.priority}`}
          >
            {priorityLabel}
          </span>

          <span className="action-target">
            <AIText>{action.target_name}</AIText>
          </span>

        </div>

        <h4>
          <AIText>{action.action}</AIText>
        </h4>

        <p>
          <AIText>{action.reason}</AIText>
        </p>

      </div>

    </article>
  );
}



/* -------------------------------- */
/* Helpers */
/* -------------------------------- */

function decodeHtmlEntities(value: string) {
  if (!value) {
    return "";
  }

  if (typeof window === "undefined") {
    return value;
  }

  const textarea = document.createElement("textarea");
  textarea.innerHTML = value;

  return textarea.value;
}

function normalizeAIText(value: string) {
  if (!value) {
    return "";
  }

  let text = decodeHtmlEntities(String(value));

  text = text
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");

  text = text
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return text;
}

function formatAIValue(
  value: number,
  type: "number" | "percent"
) {
  if (type === "percent") {
    return value.toFixed(2);
  }

  return value.toLocaleString("vi-VN", {
    maximumFractionDigits: 2,
  });
}

function AIText({
  children,
}: {
  children: string;
}) {
  const text = normalizeAIText(children);

  const parts = text.split(/(\*\*.*?\*\*)/g);

  return (
    <>
      {parts.map((part, index) => {
        if (
          part.startsWith("**") &&
          part.endsWith("**")
        ) {
          return (
            <strong key={index}>
              {part.slice(2, -2)}
            </strong>
          );
        }

        return (
          <span key={index}>
            {part}
          </span>
        );
      })}
    </>
  );
}

function getFieldLabel(
  field:
    | "product"
    | "model"
    | "component"
    | "supplier"
    | "batch"
) {
  switch (field) {
    case "product":
      return "Product";

    case "model":
      return "Model";

    case "component":
      return "Component";

    case "supplier":
      return "Supplier";

    case "batch":
      return "Batch";

    default:
      return "Item";
  }
}



function formatDate(
  date: string
) {
  const parts =
    date.split("-");

  if (parts.length !== 3) {
    return date;
  }

  return `${parts[2]}/${parts[1]}`;
}
