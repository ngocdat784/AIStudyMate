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

type MachinePerformance = {
  machine: string;
  production: number;
  defects: number;
  defect_rate: number;
  downtime: number;
};

type DailyProduction = {
  date: string;
  production: number;
  defects: number;
  defect_rate: number;
  downtime: number;
};

type DataScope = {
  has_machine_data: boolean;
  has_defect_data: boolean;
  has_downtime_data: boolean;
  has_date_data: boolean;
};

type ProductionResult = {
  title: string;
  summary: string;

  kpis: KPI[];

  insights: string[];
  warnings: string[];
  recommendations: string[];

  machine_performance: MachinePerformance[];
  daily_production: DailyProduction[];

  data_scope: DataScope;
};

export default function Home() {
  const [selectedFile, setSelectedFile] =
    useState<File | null>(null);

  const [result, setResult] =
    useState<ProductionResult | null>(null);

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
          "http://127.0.0.1:8000/api/production/analyze",
          {
            method: "POST",
            body: formData,
          }
        );

      if (!response.ok) {
        let message =
          "Không thể phân tích dữ liệu sản xuất.";

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

      const data: ProductionResult =
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
        "production-file"
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

  const totalProduction =
    getKPI("Tổng sản lượng");

  const defectRate =
    getKPI("Tỷ lệ lỗi tổng thể") ||
    getKPI("Tỷ lệ lỗi");

  const totalDowntime =
    getKPI(
      "Tổng thời gian dừng máy"
    );

  const machineCount =
    getKPI("Số lượng máy");

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
              TX
            </div>

            <div>
              <h1>TextileAI</h1>

              <span>
                Production Intelligence
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
            AI Production Analytics
          </div>

          <h2>
            Understand your production
            <br />
            with{" "}
            <span>
              data-driven insights.
            </span>
          </h2>

          <p>
            Upload dữ liệu sản xuất từ
            CSV hoặc Excel. TextileAI sẽ
            tính toán KPI, phân tích theo
            máy và theo ngày, sau đó tạo
            các nhận định bằng AI.
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
                  Production data
                </h3>

                <p>
                  Upload dữ liệu sản xuất
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
              htmlFor="production-file"
              className="production-upload"
            >

              <div className="upload-plus">
                +
              </div>

              <div>

                <strong>
                  Upload production data
                </strong>

                <span>
                  CSV hoặc XLSX · Max 10MB
                </span>

              </div>

            </label>

            <input
              id="production-file"
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

                KPI · Quality · Downtime · Insights

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
                    Analyze production
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
              TextileAI is analyzing production data
            </h3>

            <p>
              Calculating KPIs and generating
              production insights...
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
                  PRODUCTION ANALYSIS
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
                unit="%"
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
                label="Số lượng máy"
                value={
                  machineCount
                    ? Number(
                        machineCount.value
                      ).toString()
                    : "--"
                }
                unit={
                  machineCount?.unit ||
                  "máy"
                }
              />

            </section>


            {/* -------------------------------- */}
            {/* Daily production */}
            {/* -------------------------------- */}

            {result.daily_production.length >
              0 && (
              <section className="dashboard-card">

                <div className="dashboard-card-header">

                  <div>

                    <span>
                      01
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
            {/* Machines */}
            {/* -------------------------------- */}

            {result.machine_performance.length >
              0 && (
              <section className="dashboard-card">

                <div className="dashboard-card-header">

                  <div>

                    <span>
                      02
                    </span>

                    <h3>
                      Phân tích theo máy
                    </h3>

                  </div>

                  <p>
                    Machine performance
                  </p>

                </div>


                <div className="machine-table-wrapper">

                  <table className="machine-table">

                    <thead>

                      <tr>

                        <th>
                          Machine
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

                      {result.machine_performance.map(
                        (machine) => (
                          <tr
                            key={
                              machine.machine
                            }
                          >

                            <td>
                              <strong>
                                {machine.machine}
                              </strong>
                            </td>

                            <td>
                              {machine.production.toLocaleString(
                                "vi-VN"
                              )}
                            </td>

                            <td>
                              {machine.defects.toLocaleString(
                                "vi-VN"
                              )}
                            </td>

                            <td>

                              <span className="rate-value">
                                {machine.defect_rate.toFixed(
                                  2
                                )}
                                %
                              </span>

                            </td>

                            <td>
                              {
                                machine.downtime
                              }{" "}
                              phút
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
                number="03"
                title="AI Insights"
                items={
                  result.insights
                }
                className="insights"
              />

              <AISection
                number="04"
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
              number="05"
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
                Generated by TextileAI
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