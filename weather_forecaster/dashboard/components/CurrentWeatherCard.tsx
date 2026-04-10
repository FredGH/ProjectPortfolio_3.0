"use client";

import { CurrentWeather } from "@/lib/types";

function windDeg(deg: number): string {
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return dirs[Math.round(deg / 45) % 8];
}

function fmt(val: number | null | undefined, unit: string, decimals = 1): string {
  if (val == null) return "—";
  return `${val.toFixed(decimals)} ${unit}`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function StatItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-gray-400 uppercase tracking-wide">{label}</span>
      <span className="text-sm font-semibold text-gray-800">{value}</span>
    </div>
  );
}

export default function CurrentWeatherCard({ w }: { w: CurrentWeather }) {
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6 flex flex-col gap-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium text-gray-400 uppercase tracking-widest mb-1">
            {w.city_name}{w.country_code ? `, ${w.country_code}` : ""}
          </p>
          <p className="text-5xl font-bold text-gray-900 leading-none">
            {w.current_temp_c != null ? `${w.current_temp_c.toFixed(1)}°C` : "—"}
          </p>
          <p className="mt-2 text-sm text-gray-500 capitalize">
            {w.current_cloud_description} · {w.current_wind_description}
          </p>
        </div>
        <div className="text-right text-xs text-gray-400 mt-1">
          <div>Observed</div>
          <div className="font-medium text-gray-600">{fmtTime(w.observed_at)}</div>
        </div>
      </div>

      {/* Current conditions grid */}
      <div className="grid grid-cols-3 gap-4 border-t border-gray-50 pt-4">
        <StatItem label="Feels like"  value={fmt(w.feels_like_c,     "°C")} />
        <StatItem label="Humidity"    value={fmt(w.humidity_pct,     "%", 0)} />
        <StatItem label="Pressure"    value={fmt(w.pressure_hpa,     "hPa", 0)} />
        <StatItem label="Wind"        value={`${fmt(w.wind_speed_ms, "m/s")} ${windDeg(w.wind_direction_deg)}`} />
        <StatItem label="Cloud"       value={fmt(w.cloud_cover_pct,  "%", 0)} />
        <StatItem label="Visibility"  value={w.visibility_m != null ? `${(w.visibility_m / 1000).toFixed(1)} km` : "—"} />
        <StatItem label="Sunrise"     value={fmtTime(w.sunrise_at)} />
        <StatItem label="Sunset"      value={fmtTime(w.sunset_at)} />
        <StatItem label="Daily range" value={`${fmt(w.temp_min_c, "°C")} – ${fmt(w.temp_max_c, "°C")}`} />
      </div>

      {/* 24h forecast summary */}
      {w.avg_temp_c_24h != null && (
        <div className="border-t border-gray-50 pt-4">
          <p className="text-xs font-medium text-gray-400 uppercase tracking-widest mb-3">
            Next 24 h
          </p>
          <div className="grid grid-cols-3 gap-4">
            <StatItem label="Avg temp"   value={fmt(w.avg_temp_c_24h,         "°C")} />
            <StatItem label="Range"      value={`${fmt(w.min_temp_c_24h, "°C")} – ${fmt(w.max_temp_c_24h, "°C")}`} />
            <StatItem label="Avg wind"   value={fmt(w.avg_wind_speed_ms_24h,  "m/s")} />
            <StatItem label="Avg cloud"  value={fmt(w.avg_cloud_cover_pct_24h,"%", 0)} />
            <StatItem label="Conditions" value={w.predominant_cloud_description ?? "—"} />
          </div>
        </div>
      )}
    </div>
  );
}
