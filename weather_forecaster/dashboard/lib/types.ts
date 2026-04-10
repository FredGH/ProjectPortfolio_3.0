export interface CurrentWeather {
  city_name: string;
  country_code: string;
  state: string | null;
  lat: number;
  lon: number;
  observed_at: string;
  current_temp_c: number;
  feels_like_c: number;
  temp_min_c: number;
  temp_max_c: number;
  humidity_pct: number;
  pressure_hpa: number;
  visibility_m: number;
  wind_speed_ms: number;
  wind_direction_deg: number;
  cloud_cover_pct: number;
  current_cloud_description: string;
  current_wind_description: string;
  sunrise_at: string;
  sunset_at: string;
  avg_temp_c_24h: number | null;
  max_temp_c_24h: number | null;
  min_temp_c_24h: number | null;
  avg_wind_speed_ms_24h: number | null;
  avg_cloud_cover_pct_24h: number | null;
  predominant_cloud_description: string | null;
  predominant_wind_description: string | null;
  forecast_window_start: string | null;
  forecast_window_end: string | null;
}

export interface ForecastInterval {
  lat: number;
  lon: number;
  forecast_at: string;
  hours_from_now: number;
  temp_c: number;
  feels_like_c: number;
  humidity_pct: number;
  wind_speed_ms: number;
  wind_direction_deg: number;
  cloud_cover_pct: number;
  cloud_description: string;
  wind_description: string;
}

export interface HistoryObservation {
  city_name: string;
  country_code: string;
  lat: number;
  lon: number;
  observed_at: string;
  temp_c: number;
  feels_like_c: number;
  humidity_pct: number;
  pressure_hpa: number;
  wind_speed_ms: number;
  cloud_cover_pct: number;
  cloud_description: string;
  wind_description: string;
}
