
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
    setError("");
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

  // --------------------------------
  // Clear
  // --------------------------------

  const handleClear = () => {
    setSelectedFile(null);
    setResult(null);
    setError("");

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
                disabled={loading}
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
                  {result.title}
                </h2>

                <p>
                  {result.summary}
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
              />

              <KPICard
                label="Tỷ lệ lỗi"
                value={
                  defectRate
                    ? Number(
                        defectRate.value
                      ).toFixed(2)
                    : "--"
                }
                unit={
                  defectRate?.unit ||
                  "%"
                }
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
}: {
  label: string;
  value: string;
  unit: string;
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

    </div>
  );
}



/* -------------------------------- */
/* Data Quality Card */
/* -------------------------------- */

function DataQualityCard({
  dataQuality,
}: {
  dataQuality: DataQuality;
}) {
  const score =
    Number(
      dataQuality?.quality_score ?? 0
    );

  const getQualityStatus = () => {
    if (score >= 95) {
      return {
        label: "Excellent Quality",
        className: "quality-excellent",
      };
    }

    if (score >= 85) {
      return {
        label: "Good Quality",
        className: "quality-good",
      };
    }

    if (score >= 70) {
      return {
        label: "Needs Review",
        className: "quality-review",
      };
    }

    return {
      label: "Poor Quality",
      className: "quality-poor",
    };
  };

  const qualityStatus =
    getQualityStatus();

  const totalIssues =
    dataQuality.invalid_rows +
    dataQuality.empty_rows +
    dataQuality.duplicate_rows;

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
      `Có ${dataQuality.duplicate_rows} dòng trùng lặp.`
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
            {score.toFixed(0)}
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
              ? "Data quality issues detected"
              : "No data quality issues detected"}
          </span>

        </div>

      </div>



      <div className="quality-stats">

        <QualityStat
          label="Records"
          value={dataQuality.total_rows}
        />

        <QualityStat
          label="Valid"
          value={dataQuality.valid_rows}
        />

        <QualityStat
          label="Invalid"
          value={dataQuality.invalid_rows}
        />

        <QualityStat
          label="Warnings"
          value={
            dataQuality.warnings?.length ?? 0
          }
        />

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
            Dataset is ready for analysis.
          </strong>

          <span>
            {dataQuality.valid_rows} /{" "}
            {dataQuality.total_rows} records
            passed the data quality check.
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
                  {item}
                </p>

              </div>

            )
          )}

        </div>
      )}

    </section>
  );
}



/* -------------------------------- */
/* Helpers */
/* -------------------------------- */

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
