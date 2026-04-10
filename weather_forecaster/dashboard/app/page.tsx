"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { CurrentWeather } from "@/lib/types";
import CurrentWeatherCard from "@/components/CurrentWeatherCard";

const REFRESH_MS = 2 * 60 * 1000;

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

export default function DashboardPage() {
  const { data: locations, error, isLoading, isValidating } = useSWR<CurrentWeather[]>(
    "/api/current",
    fetcher,
    { refreshInterval: REFRESH_MS, revalidateOnFocus: true }
  );

  return (
    <main className="min-h-screen bg-gray-50 px-6 py-8">

      {/* Header */}
      <div className="flex items-center justify-between mb-8 max-w-7xl mx-auto">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Weather Forecaster</h1>
          <p className="text-sm text-gray-400 mt-0.5">Live conditions · refreshes every 2 min</p>
        </div>
        <Badge
          ok={!error}
          label={isValidating ? "Refreshing…" : error ? "API error" : "Live"}
        />
      </div>

      <div className="max-w-7xl mx-auto">

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
              Run the extraction pipeline first to populate the database.
            </p>
          </div>
        )}

        {/* Cards */}
        {locations && locations.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
            {locations.map((w) => (
              <CurrentWeatherCard key={`${w.lat}-${w.lon}`} w={w} />
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <footer className="mt-16 text-center text-xs text-gray-300">
        OpenWeather Free API 2.5 · Dagster · dbt · DuckDB
      </footer>
    </main>
  );
}
