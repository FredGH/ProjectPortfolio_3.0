"use client";

import { useState, useMemo } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { CurrentWeather, MonthlyTemp } from "@/lib/types";
import CurrentWeatherCard from "@/components/CurrentWeatherCard";
import TemperatureChart from "@/components/TemperatureChart";

const REFRESH_MS = 2 * 60 * 1000;
const DEFAULT_CITY = "London";

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium ${
      ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"
    }`}>
      <span className={`w-1.5 h-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`} />
      {label}
    </span>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="px-3 py-2 text-sm rounded-lg border border-gray-200 bg-white
                   focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-400
                   text-gray-800 cursor-pointer"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

export default function DashboardPage() {
  const [selectedCity, setSelectedCity] = useState<string>(DEFAULT_CITY);
  const [selectedYear, setSelectedYear] = useState<string>("");

  // ── Current weather for all capitals ──────────────────────────────────────
  const { data: locations, error, isLoading, isValidating } = useSWR<CurrentWeather[]>(
    "/api/current",
    fetcher,
    { refreshInterval: REFRESH_MS, revalidateOnFocus: true }
  );

  // ── Monthly temperature for selected city ─────────────────────────────────
  const { data: monthly } = useSWR<MonthlyTemp[]>(
    selectedCity ? `/api/monthly?city=${encodeURIComponent(selectedCity)}` : null,
    fetcher,
    { revalidateOnFocus: false }
  );

  // Sorted city list for the dropdown
  const cityOptions = useMemo(() => {
    if (!locations) return [];
    return [...locations]
      .sort((a, b) => a.city_name.localeCompare(b.city_name))
      .map((w) => ({ value: w.city_name, label: `${w.city_name} (${w.country_code})` }));
  }, [locations]);

  // Current weather card for selected city
  const currentCard = useMemo(() => {
    if (!locations) return null;
    return (
      locations.find((w) => w.city_name === selectedCity) ??
      locations.find((w) => w.city_name.toLowerCase().includes(DEFAULT_CITY.toLowerCase())) ??
      locations[0] ??
      null
    );
  }, [locations, selectedCity]);

  // Available years from monthly data
  const yearOptions = useMemo(() => {
    if (!monthly || monthly.length === 0) return [];
    const years = [...new Set(monthly.map((d) => d.year))].sort((a, b) => b - a);
    return years.map((y) => ({ value: String(y), label: String(y) }));
  }, [monthly]);

  // Auto-select the most recent year when data loads / city changes
  const effectiveYear = useMemo(() => {
    if (selectedYear && yearOptions.some((o) => o.value === selectedYear)) return selectedYear;
    return yearOptions[0]?.value ?? "";
  }, [selectedYear, yearOptions]);

  // Filter monthly data to selected year
  const chartData = useMemo(() => {
    if (!monthly || !effectiveYear) return [];
    return monthly.filter((d) => String(d.year) === effectiveYear);
  }, [monthly, effectiveYear]);

  // Handle city change: reset year selection
  function handleCityChange(city: string) {
    setSelectedCity(city);
    setSelectedYear("");
  }

  return (
    <main className="min-h-screen bg-gray-50 px-6 py-8">

      {/* Header */}
      <div className="flex items-center justify-between mb-8 max-w-7xl mx-auto">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Weather Forecaster</h1>
          <p className="text-sm text-gray-400 mt-0.5">
            World capitals · live conditions · refreshes every 2 min
          </p>
        </div>
        <Badge
          ok={!error}
          label={isValidating ? "Refreshing…" : error ? "API error" : "Live"}
        />
      </div>

      <div className="max-w-7xl mx-auto flex flex-col gap-8">

        {/* Loading */}
        {isLoading && (
          <div className="flex items-center justify-center h-48 text-gray-400 animate-pulse">
            Loading weather data…
          </div>
        )}

        {/* Error */}
        {error && !isLoading && (
          <div className="rounded-xl bg-red-50 border border-red-100 p-6 text-center">
            <p className="text-red-600 font-medium">Could not reach the API</p>
            <p className="text-red-400 text-sm mt-1">
              Make sure the Docker stack is running:{" "}
              <code className="bg-red-100 px-1.5 py-0.5 rounded text-xs">
                docker compose -f docker-compose.dagster.yml up
              </code>
            </p>
          </div>
        )}

        {/* Empty */}
        {!isLoading && !error && locations?.length === 0 && (
          <div className="rounded-xl bg-yellow-50 border border-yellow-100 p-6 text-center">
            <p className="text-yellow-700 font-medium">No data yet</p>
            <p className="text-yellow-500 text-sm mt-1">
              Run <strong>capitals_load</strong> then <strong>weather_extraction</strong> in Dagit.
            </p>
          </div>
        )}

        {!isLoading && !error && cityOptions.length > 0 && (
          <>
            {/* Capital selector */}
            <div className="w-72">
              <SelectField
                label={`Select a capital (${cityOptions.length} available)`}
                value={currentCard?.city_name ?? selectedCity}
                onChange={handleCityChange}
                options={cityOptions}
              />
            </div>

            {/* Side-by-side: current weather card + monthly chart */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 items-start">

              {/* Left — current weather card */}
              {currentCard ? (
                <CurrentWeatherCard w={currentCard} />
              ) : (
                <div className="rounded-xl bg-gray-50 border border-gray-100 p-6 text-center text-gray-400 text-sm">
                  No weather data available for {selectedCity}.
                </div>
              )}

              {/* Right — monthly temperature chart */}
              <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <p className="text-xs font-medium text-gray-400 uppercase tracking-widest mb-0.5">
                      Monthly temperature
                    </p>
                    <p className="text-sm text-gray-600">
                      {currentCard?.city_name ?? selectedCity}
                    </p>
                  </div>

                  {yearOptions.length > 0 && (
                    <div className="w-28">
                      <SelectField
                        label="Year"
                        value={effectiveYear}
                        onChange={setSelectedYear}
                        options={yearOptions}
                      />
                    </div>
                  )}
                </div>

                {!monthly && (
                  <div className="flex items-center justify-center h-48 text-gray-400 text-sm animate-pulse">
                    Loading historical data…
                  </div>
                )}

                {monthly && yearOptions.length === 0 && (
                  <div className="flex items-center justify-center h-48 text-gray-400 text-sm">
                    No historical observations recorded yet.
                  </div>
                )}

                {monthly && effectiveYear && (
                  <TemperatureChart data={chartData} year={Number(effectiveYear)} />
                )}
              </div>

            </div>
          </>
        )}
      </div>

      {/* Footer */}
      <footer className="mt-16 text-center text-xs text-gray-300">
        OpenWeather Free API 2.5 · Dagster · dbt · DuckDB · {locations?.length ?? 0} capitals
      </footer>
    </main>
  );
}
