type IllustrationKind =
  | "data"
  | "dashboard"
  | "history"
  | "production"
  | "quality"
  | "equipment"
  | "materials"
  | "improvement";

const illustrations: Record<
  IllustrationKind,
  {
    title: string;
    metric: string;
    value: string;
    note: string;
    bars: number[];
  }
> = {
  data: {
    title: "Chất lượng dữ liệu",
    metric: "Dòng hợp lệ",
    value: "94%",
    note: "Đã kiểm tra cấu trúc",
    bars: [42, 63, 54, 77, 66, 88, 72],
  },
  dashboard: {
    title: "Tổng quan sản xuất",
    metric: "Sản lượng",
    value: "1.240",
    note: "Theo khoảng thời gian",
    bars: [48, 57, 48, 74, 67, 91, 82],
  },
  history: {
    title: "Theo dõi phân tích",
    metric: "Báo cáo",
    value: "12",
    note: "Có thể mở lại",
    bars: [30, 45, 54, 49, 69, 78, 89],
  },
  production: {
    title: "Nhịp sản xuất",
    metric: "Sản lượng",
    value: "Theo ngày",
    note: "So với kế hoạch",
    bars: [40, 58, 51, 72, 64, 87, 78],
  },
  quality: {
    title: "Chất lượng theo nhóm",
    metric: "Tỷ lệ lỗi",
    value: "Theo model",
    note: "So sánh theo lô",
    bars: [82, 61, 72, 43, 55, 33, 47],
  },
  equipment: {
    title: "Thời gian dừng máy",
    metric: "Downtime",
    value: "Theo máy",
    note: "Tín hiệu cần kiểm tra",
    bars: [30, 70, 46, 85, 58, 42, 69],
  },
  materials: {
    title: "Nhu cầu vật liệu",
    metric: "Tồn kho",
    value: "Theo kỳ",
    note: "Đối chiếu nhu cầu",
    bars: [88, 77, 67, 58, 48, 37, 29],
  },
  improvement: {
    title: "Đánh giá can thiệp",
    metric: "Trước / sau",
    value: "Đối chiếu",
    note: "Dựa trên KPI thực tế",
    bars: [43, 47, 45, 59, 67, 73, 82],
  },
};

export default function HomeFeatureIllustration({
  kind,
}: {
  kind: IllustrationKind;
}) {
  const illustration = illustrations[kind];

  return (
    <figure className="home-feature-visual">
      <svg
        viewBox="0 0 560 370"
        role="img"
        aria-label={`Minh họa HardwareAI: ${illustration.title}`}
      >
        <rect
          x="1"
          y="1"
          width="558"
          height="368"
          rx="25"
          fill="var(--surface-subtle)"
        />
        <rect
          x="25"
          y="24"
          width="510"
          height="322"
          rx="19"
          fill="var(--surface)"
          stroke="var(--line)"
          strokeWidth="1.5"
        />
        <circle cx="49" cy="48" r="4" fill="var(--blue)" />
        <circle cx="63" cy="48" r="4" fill="var(--blue)" opacity=".5" />
        <circle cx="77" cy="48" r="4" fill="var(--blue)" opacity=".25" />
        <text
          x="97"
          y="52"
          fill="var(--ink-muted)"
          fontSize="12"
          fontWeight="700"
          fontFamily="Segoe UI, Arial, sans-serif"
        >
          HARDWAREAI · MINH HỌA
        </text>
        <rect
          x="427"
          y="37"
          width="83"
          height="22"
          rx="11"
          fill="var(--color-primary-soft)"
        />
        <text
          x="468.5"
          y="52"
          textAnchor="middle"
          fill="var(--blue-dark)"
          fontSize="9"
          fontWeight="800"
          fontFamily="Segoe UI, Arial, sans-serif"
        >
          THEO DỮ LIỆU
        </text>
        <line x1="25" y1="72" x2="535" y2="72" stroke="var(--line)" />
        <rect
          x="44"
          y="91"
          width="304"
          height="231"
          rx="14"
          fill="var(--surface)"
          stroke="var(--line)"
        />
        <text
          x="62"
          y="119"
          fill="var(--ink)"
          fontSize="14"
          fontWeight="700"
          fontFamily="Segoe UI, Arial, sans-serif"
        >
          {illustration.title}
        </text>
        <text
          x="62"
          y="140"
          fill="var(--ink-muted)"
          fontSize="10"
          fontFamily="Segoe UI, Arial, sans-serif"
        >
          Xu hướng · khoảng thời gian đã chọn
        </text>
        {[174, 211, 248, 285].map((y) => (
          <line
            key={y}
            x1="62"
            y1={y}
            x2="330"
            y2={y}
            stroke="var(--line)"
            strokeDasharray="3 5"
          />
        ))}
        {illustration.bars.map((height, index) => (
          <rect
            key={`${height}-${index}`}
            x={71 + index * 35}
            y={285 - height * 1.05}
            width="18"
            height={height * 1.05}
            rx="5"
            fill="var(--blue)"
            opacity={0.45 + index * 0.07}
          />
        ))}
        <polyline
          points={illustration.bars
            .map((height, index) => `${80 + index * 35},${278 - height * 0.72}`)
            .join(" ")}
          fill="none"
          stroke="var(--blue-dark)"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <rect
          x="365"
          y="91"
          width="150"
          height="231"
          rx="14"
          fill="var(--surface-subtle)"
          stroke="var(--line)"
        />
        <text
          x="383"
          y="120"
          fill="var(--ink-muted)"
          fontSize="10"
          fontWeight="700"
          fontFamily="Segoe UI, Arial, sans-serif"
        >
          CHỈ SỐ NỔI BẬT
        </text>
        <text
          x="383"
          y="162"
          fill="var(--ink)"
          fontSize="25"
          fontWeight="750"
          fontFamily="Segoe UI, Arial, sans-serif"
        >
          {illustration.value}
        </text>
        <text
          x="383"
          y="183"
          fill="var(--ink-muted)"
          fontSize="11"
          fontFamily="Segoe UI, Arial, sans-serif"
        >
          {illustration.metric}
        </text>
        <line x1="383" y1="202" x2="497" y2="202" stroke="var(--line)" />
        <circle cx="390" cy="226" r="4" fill="var(--blue)" />
        <text
          x="402"
          y="230"
          fill="var(--ink-muted)"
          fontSize="10"
          fontFamily="Segoe UI, Arial, sans-serif"
        >
          {illustration.note}
        </text>
        <rect
          x="383"
          y="250"
          width="114"
          height="7"
          rx="4"
          fill="var(--line)"
        />
        <rect
          x="383"
          y="250"
          width="76"
          height="7"
          rx="4"
          fill="var(--blue)"
        />
        <rect
          x="383"
          y="272"
          width="114"
          height="7"
          rx="4"
          fill="var(--line)"
        />
        <rect
          x="383"
          y="272"
          width="53"
          height="7"
          rx="4"
          fill="var(--blue-dark)"
          opacity=".8"
        />
        <text
          x="383"
          y="304"
          fill="var(--ink-muted)"
          fontSize="9"
          fontFamily="Segoe UI, Arial, sans-serif"
        >
          Ví dụ giao diện, không phải số liệu thực
        </text>
      </svg>
    </figure>
  );
}
