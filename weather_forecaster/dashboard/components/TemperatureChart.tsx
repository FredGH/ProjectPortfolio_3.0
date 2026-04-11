"use client";

import { MonthlyTemp } from "@/lib/types";

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const CHART_H   = 180;   // px — drawable area height
const BAR_W     = 28;    // px — avg bar width
const RANGE_W   = 4;     // px — min/max range bar width
const PADDING_L = 40;    // px — left axis
const PADDING_B = 28;    // px — bottom labels
const PADDING_T = 16;    // px — top
const COL_W     = 48;    // px — column width per month

function tempColor(t: number): string {
  if (t <= 0)   return "#93c5fd"; // blue-300
  if (t <= 10)  return "#6ee7b7"; // emerald-300
  if (t <= 20)  return "#fde68a"; // amber-200
  if (t <= 30)  return "#fb923c"; // orange-400
  return "#ef4444";               // red-500
}

interface Props {
  data: MonthlyTemp[];   // all months for the selected year (may have gaps)
  year: number;
}

export default function TemperatureChart({ data, year }: Props) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-400 text-sm">
        No observations recorded for {year}.
      </div>
    );
  }

  // Index data by month (1-based)
  const byMonth: Record<number, MonthlyTemp> = {};
  for (const d of data) byMonth[d.month] = d;

  // Y-axis domain
  const allAvg = data.map((d) => d.avg_temp_c);
  const allMin = data.map((d) => d.min_temp_c);
  const allMax = data.map((d) => d.max_temp_c);
  const rawMin = Math.min(...allMin);
  const rawMax = Math.max(...allMax);
  const pad    = Math.max(3, (rawMax - rawMin) * 0.15);
  const yMin   = Math.floor(rawMin - pad);
  const yMax   = Math.ceil(rawMax + pad);
  const yRange = yMax - yMin || 1;

  const toY = (v: number) =>
    PADDING_T + CHART_H - ((v - yMin) / yRange) * CHART_H;

  const svgW = PADDING_L + 12 * COL_W + 8;

  // Y gridlines at every 5 °C
  const gridStep = 5;
  const gridLines: number[] = [];
  for (let g = Math.ceil(yMin / gridStep) * gridStep; g <= yMax; g += gridStep) {
    gridLines.push(g);
  }

  return (
    <div className="overflow-x-auto">
      <svg
        width={svgW}
        height={PADDING_T + CHART_H + PADDING_B}
        className="block"
        style={{ minWidth: svgW }}
      >
        {/* Grid lines */}
        {gridLines.map((g) => {
          const y = toY(g);
          return (
            <g key={g}>
              <line
                x1={PADDING_L} y1={y}
                x2={svgW - 8}  y2={y}
                stroke="#e5e7eb" strokeWidth={1}
              />
              <text
                x={PADDING_L - 6} y={y + 4}
                textAnchor="end" fontSize={10} fill="#9ca3af"
              >
                {g}°
              </text>
            </g>
          );
        })}

        {/* Zero line (if in range) */}
        {yMin <= 0 && yMax >= 0 && (
          <line
            x1={PADDING_L} y1={toY(0)}
            x2={svgW - 8}  y2={toY(0)}
            stroke="#d1d5db" strokeWidth={1.5} strokeDasharray="4 3"
          />
        )}

        {/* Bars per month */}
        {MONTH_LABELS.map((label, i) => {
          const month = i + 1;
          const cx = PADDING_L + i * COL_W + COL_W / 2;
          const d  = byMonth[month];

          return (
            <g key={month}>
              {/* Month label */}
              <text
                x={cx} y={PADDING_T + CHART_H + 16}
                textAnchor="middle" fontSize={10}
                fill={d ? "#374151" : "#d1d5db"}
              >
                {label}
              </text>

              {d && (() => {
                const avgY  = toY(d.avg_temp_c);
                const minY  = toY(d.min_temp_c);
                const maxY  = toY(d.max_temp_c);
                const barX  = cx - BAR_W / 2;
                const barH  = Math.max(2, Math.abs(toY(0 > d.avg_temp_c ? d.avg_temp_c : 0) - avgY));
                const baseY = toY(Math.max(0, d.avg_temp_c > 0 ? 0 : d.avg_temp_c));

                return (
                  <>
                    {/* Min-Max range bar */}
                    <rect
                      x={cx - RANGE_W / 2} y={maxY}
                      width={RANGE_W} height={Math.max(2, minY - maxY)}
                      fill="#cbd5e1" rx={2}
                    />
                    {/* Avg bar */}
                    <rect
                      x={barX} y={Math.min(avgY, baseY)}
                      width={BAR_W} height={barH}
                      fill={tempColor(d.avg_temp_c)} rx={3}
                      opacity={0.9}
                    />
                    {/* Avg label */}
                    <text
                      x={cx} y={avgY - 4}
                      textAnchor="middle" fontSize={9} fill="#374151" fontWeight={600}
                    >
                      {d.avg_temp_c}°
                    </text>
                  </>
                );
              })()}
            </g>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="flex items-center gap-4 mt-2 text-xs text-gray-400 pl-10">
        <span className="flex items-center gap-1">
          <span className="inline-block w-5 h-3 rounded bg-amber-200" /> Avg temp
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-2 h-3 rounded bg-slate-300" /> Min – Max range
        </span>
      </div>
    </div>
  );
}
