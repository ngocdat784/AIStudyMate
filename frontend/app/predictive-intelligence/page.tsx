"use client";

import { useState } from "react";

import FileDropzone from "../../components/file-dropzone";
import { apiFetch } from "../../lib/api";

type ForecastPoint = {
  period: string;
  value: number | null;
  prediction_interval?: {
    lower: number | null;
    upper: number | null;
    level_percent: number;
    coverage_guaranteed: boolean;
  } | null;
};

type Forecast = {
  status: string;
  forecast?: ForecastPoint[];
  observed_count?: number;
  feature_row_count?: number;
  model_type?: string;
  selected_method?: string;
  reason?: string;
  backtest?: {
    sample_count: number;
    mae: number | null;
    rmse: number | null;
    smape_percent: number | null;
  } | null;
  temporal_split?: Record<
    string,
    { count: number; start: string | null; end: string | null }
  >;
  confidence_assessment?: { level: string; is_probability: boolean };
};

type MaterialConsumption = {
  status: string;
  by_material: Array<
    Forecast & {
      material: string;
      unit: string | null;
      material_key: string;
    }
  >;
};

type PredictiveAnalysis = {
  analysis_history_id: number;
  forecast_horizon_periods: number;
  predictive_data_foundation: {
    status: string;
    source_row_count: number;
    observed_period_count: number;
    calendar_coverage_percent: number | null;
    training_dataset: {
      leakage_controls: Record<string, boolean>;
      temporal_splits_by_metric: Record<
        string,
        Record<string, { count: number; start: string | null; end: string | null }>
      >;
    };
  };
  forecasts: Record<string, Forecast | MaterialConsumption | Forecast[]>;
  production_plan_completion: {
    status: string;
    threshold_percent: number;
    daily_assessment: Array<{
      period: string;
      value: number;
      status: string;
    }>;
    interpretation: string;
  };
  quality_risk: {
    threshold_percent: number;
    threshold_signals: Array<{
      period: string;
      value: number;
      status: string;
    }>;
  };
  machine_failure_assessment: {
    status: string;
    observed_period_count: number;
    failure_period_count: number;
    empirical_failure_probability_percent: number | null;
    interpretation: string;
    by_machine: Array<{
      machine: string;
      status: string;
      observed_period_count: number;
      failure_period_count: number;
      empirical_failure_probability_percent: number | null;
    }>;
  };
  material_quality_risk: {
    threshold_percent: number;
    by_material: Array<
      Forecast & {
        material: string;
        unit: string | null;
        material_key: string;
        threshold_signals: Array<{
          period: string;
          value: number;
          status: string;
        }>;
      }
    >;
  };
  material_shortage_risk: Array<{
    material: string;
    unit: string | null;
    status: string;
    available_stock?: number;
    forecast_horizon_consumption?: number;
    projected_remaining_stock?: number;
    shortage_quantity?: number;
    reason?: string;
  }>;
  scenario_simulation: {
    method: string;
    scenarios: Record<
      string,
      {
        status: string;
        forecast_production?: number | null;
        forecast_defect_rate_percent?: number | null;
        forecast_good_units?: number | null;
        assumption: string;
      }
    >;
  };
  model_performance_monitoring: Record<
    string,
    {
      backtest?: Forecast["backtest"];
      drift?: { status: string; relative_change_percent: number | null };
      confidence?: { level: string; is_probability: boolean };
    }
  >;
  analysis_limitations: string[];
};

const forecastMetrics = [
  ["production", "Sản lượng"],
  ["throughput", "Throughput"],
  ["performance_percent", "Hiệu suất kế hoạch (%)"],
  ["defect_rate_percent", "Tỷ lệ lỗi (%)"],
  ["downtime_minutes", "Downtime (phút)"],
  ["available_time_minutes", "Thời gian khả dụng (phút)"],
  ["machine_failure_count", "Số ngày có tín hiệu lỗi"],
  ["unplanned_downtime_events", "Sự kiện dừng ngoài kế hoạch"],
] as const;

function formatted(value: number | null | undefined, digits = 2): string {
  return value == null
    ? "Chưa đủ dữ liệu"
    : value.toLocaleString("vi-VN", { maximumFractionDigits: digits });
}

function getForecast(
  forecasts: PredictiveAnalysis["forecasts"],
  key: string,
): Forecast | undefined {
  const value = forecasts[key];
  return value && !Array.isArray(value) && !("by_material" in value)
    ? value
    : undefined;
}

function getMaterialForecasts(
  forecasts: PredictiveAnalysis["forecasts"],
): MaterialConsumption["by_material"] {
  const value = forecasts.material_consumption;
  return value && !Array.isArray(value) && "by_material" in value
    ? value.by_material
    : [];
}

export default function PredictiveIntelligencePage() {
  const [file, setFile] = useState<File | null>(null);
  const [sourceTimezone, setSourceTimezone] = useState("UTC");
  const [downtimeUnit, setDowntimeUnit] = useState("minutes");
  const [durationUnit, setDurationUnit] = useState("minutes");
  const [horizon, setHorizon] = useState(7);
  const [defectThreshold, setDefectThreshold] = useState(5);
  const [capacityChange, setCapacityChange] = useState(0);
  const [downtimeReduction, setDowntimeReduction] = useState(0);
  const [materialShortage, setMaterialShortage] = useState(0);
  const [qualityImprovement, setQualityImprovement] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<PredictiveAnalysis | null>(null);

  async function submitAnalysis(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setLoading(true);
    setError("");
    setResult(null);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("source_timezone", sourceTimezone);
    formData.append("downtime_unit", downtimeUnit);
    formData.append("duration_unit", durationUnit);
    formData.append("horizon", String(horizon));
    formData.append("defect_threshold_percent", String(defectThreshold));
    formData.append("capacity_change_percent", String(capacityChange));
    formData.append("downtime_reduction_percent", String(downtimeReduction));
    formData.append("material_shortage_percent", String(materialShortage));
    formData.append(
      "quality_improvement_percentage_points",
      String(qualityImprovement),
    );
    try {
      const response = await apiFetch("/api/predictive-intelligence/analyze", {
        method: "POST",
        body: formData,
      });
      const body: unknown = await response.json();
      if (!response.ok) {
        const detail =
          typeof body === "object" &&
          body !== null &&
          "detail" in body &&
          typeof body.detail === "string"
            ? body.detail
            : `Dự báo thất bại (HTTP ${response.status}).`;
        throw new Error(detail);
      }
      setResult(body as PredictiveAnalysis);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Không thể phân tích dữ liệu dự báo.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="executive-page">
      <div className="executive-content">
        <header className="executive-header">
          <div>
            <span className="executive-eyebrow">HARDWAREAI · PHASE 2.6</span>
            <h1>Predictive Intelligence</h1>
            <p>
              Dự báo thống kê có kiểm định theo thời gian; khi dữ liệu chưa đủ,
              hệ thống nêu rõ giới hạn thay vì ép dùng machine learning.
            </p>
          </div>
        </header>

        <form className="executive-card production-upload" onSubmit={submitAnalysis}>
          <FileDropzone
            accept=".csv,.xlsx,.xlsm,.xls"
            title="File sản xuất / chất lượng / thiết bị / vật liệu"
            files={file ? [file] : []}
            onFilesSelected={(selected) => {
              setFile(selected[0] ?? null);
              setResult(null);
              setError("");
            }}
          />
          <label>
            Source timezone (IANA)
            <input
              value={sourceTimezone}
              onChange={(event) => setSourceTimezone(event.target.value)}
              placeholder="UTC hoặc Asia/Ho_Chi_Minh"
            />
          </label>
          <div className="production-unit-fields">
            <label>
              Downtime unit
              <select
                value={downtimeUnit}
                onChange={(event) => setDowntimeUnit(event.target.value)}
              >
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
            <label>
              Available / operating time unit
              <select
                value={durationUnit}
                onChange={(event) => setDurationUnit(event.target.value)}
              >
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
            <label>
              Horizon (ngày)
              <input
                type="number"
                min={1}
                max={30}
                value={horizon}
                onChange={(event) => setHorizon(Number(event.target.value))}
              />
            </label>
            <label>
              Ngưỡng tỷ lệ lỗi (%)
              <input
                type="number"
                min={0}
                max={100}
                step="any"
                value={defectThreshold}
                onChange={(event) =>
                  setDefectThreshold(Number(event.target.value))
                }
              />
            </label>
            <label>
              Tăng công suất giả định (%)
              <input
                type="number"
                min={-100}
                max={200}
                step="any"
                value={capacityChange}
                onChange={(event) => setCapacityChange(Number(event.target.value))}
              />
            </label>
            <label>
              Giảm downtime giả định (%)
              <input
                type="number"
                min={0}
                max={100}
                step="any"
                value={downtimeReduction}
                onChange={(event) =>
                  setDowntimeReduction(Number(event.target.value))
                }
              />
            </label>
            <label>
              Thiếu vật liệu giả định (%)
              <input
                type="number"
                min={0}
                max={100}
                step="any"
                value={materialShortage}
                onChange={(event) =>
                  setMaterialShortage(Number(event.target.value))
                }
              />
            </label>
            <label>
              Cải thiện lỗi giả định (điểm %)
              <input
                type="number"
                min={0}
                max={100}
                step="any"
                value={qualityImprovement}
                onChange={(event) =>
                  setQualityImprovement(Number(event.target.value))
                }
              />
            </label>
          </div>
          <button type="submit" disabled={loading || !file}>
            {loading ? "Đang phân tích…" : "Phân tích và dự báo"}
          </button>
          {error && <p className="executive-error" role="alert">{error}</p>}
        </form>

        {loading && (
          <p className="executive-loading" role="status">
            Đang chuẩn hóa dữ liệu và kiểm định dự báo theo thứ tự thời gian…
          </p>
        )}

        {result && (
          <>
            <section className="executive-card">
              <span className="executive-eyebrow">DATA FOUNDATION</span>
              <h2>
                {result.predictive_data_foundation.status ===
                "adequate_for_statistical_baseline"
                  ? "Đủ dữ liệu cho baseline thống kê"
                  : "Dữ liệu lịch sử chưa đủ"}
              </h2>
              <p>
                {result.predictive_data_foundation.observed_period_count} ngày
                có dữ liệu · độ phủ lịch{" "}
                {formatted(
                  result.predictive_data_foundation.calendar_coverage_percent,
                )}
                % · {result.predictive_data_foundation.source_row_count} dòng
                nguồn · dự báo {result.forecast_horizon_periods} ngày.
              </p>
              <p>
                Chia train / validation / test theo thời gian; chỉ baseline
                thống kê. Tập test không dùng để chọn phương pháp.
              </p>
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <h2>Dự báo, độ tin cậy và sai số holdout</h2>
              </div>
              <div className="production-table-scroll">
                <table className="production-table">
                  <thead>
                    <tr>
                      <th>Chỉ số</th>
                      <th>Trạng thái</th>
                      <th>Phương pháp</th>
                      <th>MAE / sMAPE</th>
                      <th>Giá trị dự báo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {forecastMetrics.map(([key, title]) => {
                      const forecast = getForecast(result.forecasts, key);
                      return (
                        <tr key={key}>
                          <th scope="row">{title}</th>
                          <td>
                            {forecast?.status === "forecasted"
                              ? forecast.confidence_assessment?.level ?? "Đã dự báo"
                              : "Chưa đủ dữ liệu"}
                          </td>
                          <td>
                            {forecast?.selected_method ?? forecast?.reason ?? "—"}
                          </td>
                          <td>
                            {forecast?.backtest
                              ? `${formatted(forecast.backtest.mae)} / ${formatted(forecast.backtest.smape_percent)}%`
                              : "—"}
                          </td>
                          <td>
                            {forecast?.forecast?.length
                              ? forecast.forecast
                                  .map((point) => formatted(point.value))
                                  .join(" · ")
                              : "Chưa đủ dữ liệu"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p>
                Khoảng dự báo dùng sai số tuyệt đối holdout; coverage không
                được bảo đảm. Confidence là phân loại theo sai số và cỡ mẫu,
                không phải xác suất.
              </p>
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <h2>Tiêu hao và tín hiệu thiếu vật liệu</h2>
              </div>
              {getMaterialForecasts(result.forecasts).length ? (
                <div className="production-table-scroll">
                  <table className="production-table">
                    <thead>
                      <tr>
                        <th>Vật liệu</th>
                        <th>Trạng thái dự báo</th>
                        <th>Tồn kho hiện tại</th>
                        <th>Nhu cầu dự báo</th>
                        <th>Tồn kho sau kỳ</th>
                        <th>Tín hiệu</th>
                      </tr>
                    </thead>
                    <tbody>
                      {getMaterialForecasts(result.forecasts).map((material) => {
                        const risk = result.material_shortage_risk.find(
                          (item) =>
                            item.material === material.material &&
                            item.unit === material.unit,
                        );
                        const demand = material.forecast?.reduce(
                          (total, point) => total + (point.value ?? 0),
                          0,
                        );
                        return (
                          <tr key={material.material_key}>
                            <th scope="row">
                              {material.material} ({material.unit ?? "?"})
                            </th>
                            <td>
                              {material.status === "forecasted"
                                ? material.selected_method
                                : "Chưa đủ dữ liệu"}
                            </td>
                            <td>{formatted(risk?.available_stock)}</td>
                            <td>{formatted(demand)}</td>
                            <td>{formatted(risk?.projected_remaining_stock)}</td>
                            <td>{risk?.status ?? risk?.reason ?? "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="executive-empty">
                  Không có cặp Material và Actual Consumption hợp lệ.
                </p>
              )}
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <h2>Rủi ro chất lượng, thiết bị và giám sát</h2>
              </div>
              <p>
                Kế hoạch sản xuất:{" "}
                {result.production_plan_completion.status === "forecasted"
                  ? result.production_plan_completion.daily_assessment
                      .map(
                        (item) =>
                          `${item.period}: ${formatted(item.value)}% (${item.status})`,
                      )
                      .join(" · ")
                  : "Chưa đủ dữ liệu kế hoạch để dự báo."}
              </p>
              <p>{result.production_plan_completion.interpretation}</p>
              <p>
                Chỉ báo lỗi máy:{" "}
                {formatted(
                  result.machine_failure_assessment
                    .empirical_failure_probability_percent,
                )}
                % trên {result.machine_failure_assessment.observed_period_count}{" "}
                ngày quan sát ({result.machine_failure_assessment.status}).
                Đây là tần suất lịch sử, không phải dự báo xác suất đã hiệu
                chuẩn.
              </p>
              <p>{result.machine_failure_assessment.interpretation}</p>
              {result.machine_failure_assessment.by_machine.length > 0 && (
                <ul>
                  {result.machine_failure_assessment.by_machine.map((machine) => (
                    <li key={machine.machine}>
                      {machine.machine}:{" "}
                      {formatted(
                        machine.empirical_failure_probability_percent,
                      )}
                      % trên {machine.observed_period_count} ngày (
                      {machine.status})
                    </li>
                  ))}
                </ul>
              )}
              {result.material_quality_risk.by_material.length > 0 && (
                <>
                  <h3>Chất lượng dự báo theo vật liệu</h3>
                  <ul>
                    {result.material_quality_risk.by_material.map((material) => (
                      <li key={material.material_key}>
                        {material.material} ({material.unit ?? "?"}):{" "}
                        {material.status === "forecasted"
                          ? material.threshold_signals
                              .map(
                                (signal) =>
                                  `${signal.period} ${formatted(signal.value)}% (${signal.status})`,
                              )
                              .join(" · ")
                          : material.reason ?? "Chưa đủ dữ liệu"}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <ul>
                {Object.entries(result.model_performance_monitoring).map(
                  ([metric, monitoring]) => (
                    <li key={metric}>
                      {metric}: drift {monitoring.drift?.status ?? "—"} · thay
                      đổi {formatted(monitoring.drift?.relative_change_percent)}
                      % · holdout sMAPE{" "}
                      {formatted(monitoring.backtest?.smape_percent)}%
                    </li>
                  ),
                )}
              </ul>
              {result.quality_risk.threshold_signals.length > 0 && (
                <p>
                  Cảnh báo vượt ngưỡng lỗi:{" "}
                  {result.quality_risk.threshold_signals
                    .filter(
                      (signal) =>
                        signal.status === "threshold_exceedance_signal",
                    )
                    .map(
                      (signal) =>
                        `${signal.period} (${formatted(signal.value)}%)`,
                    )
                    .join(", ") || "Không có tín hiệu vượt ngưỡng."}
                </p>
              )}
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <h2>Mô phỏng what-if</h2>
              </div>
              <p>
                Mô phỏng độ nhạy theo giả định đầu vào; không phải mô hình công
                suất hay ước lượng nhân quả.
              </p>
              <ul>
                {Object.entries(result.scenario_simulation.scenarios).map(
                  ([scenario, outcome]) => (
                    <li key={scenario}>
                      {scenario}: {outcome.status} · sản lượng{" "}
                      {formatted(outcome.forecast_production)}
                      {outcome.forecast_defect_rate_percent != null
                        ? ` · tỷ lệ lỗi ${formatted(outcome.forecast_defect_rate_percent)}%`
                        : ""}
                      {" — "}
                      {outcome.assumption}
                    </li>
                  ),
                )}
              </ul>
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <h2>Giới hạn và dữ liệu cần bổ sung</h2>
              </div>
              <ul>
                {result.analysis_limitations.map((limitation) => (
                  <li key={limitation}>{limitation}</li>
                ))}
              </ul>
              <p>
                Kết quả đã lưu trong Analysis History (#{result.analysis_history_id}).
              </p>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
