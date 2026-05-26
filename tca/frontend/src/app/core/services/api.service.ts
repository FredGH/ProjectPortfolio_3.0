import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

const BASE = '/api';

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  login(clientId: string, clientSecret: string): Observable<TokenResponse> {
    const body = new URLSearchParams();
    body.set('client_id', clientId);
    body.set('client_secret', clientSecret);
    return this.http.post<TokenResponse>(`${BASE}/auth/token`, body.toString(), {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
  }

  getTcaSummary(tradeDate: string, counterpartyId?: string, instrumentClass?: string): Observable<any[]> {
    let params = new HttpParams().set('trade_date', tradeDate);
    if (counterpartyId) params = params.set('counterparty_id', counterpartyId);
    if (instrumentClass) params = params.set('instrument_class', instrumentClass);
    return this.http.get<any[]>(`${BASE}/tca/summary`, { params });
  }

  getOrderTca(orderId: string): Observable<any> {
    return this.http.get<any>(`${BASE}/tca/order/${orderId}`);
  }

  getAlgoPerformance(tradeDate: string, instrumentClass?: string): Observable<any[]> {
    let params = new HttpParams().set('trade_date', tradeDate);
    if (instrumentClass) params = params.set('instrument_class', instrumentClass);
    return this.http.get<any[]>(`${BASE}/tca/algo-performance`, { params });
  }

  getAlphaDecay(tradeDate: string): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/tca/alpha-decay`, {
      params: new HttpParams().set('trade_date', tradeDate),
    });
  }

  getPeerBenchmark(orderId: string): Observable<any> {
    return this.http.get<any>(`${BASE}/tca/peer-benchmark/${orderId}`);
  }

  getOrders(tradeDate: string): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/orders`, {
      params: new HttpParams().set('trade_date', tradeDate),
    });
  }

  getMifidExport(tradeDate: string): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/mifid/export`, {
      params: new HttpParams().set('trade_date', tradeDate),
    });
  }

  getWarnings(): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/reports/warning`);
  }

  predictSlippage(payload: {
    instrument_class: string;
    side: string;
    quantity: number;
    vol_regime: string;
    algo_id?: string;
    venue_id?: string;
    order_hour?: number;
    order_dow?: number;
  }): Observable<any> {
    return this.http.post<any>(`${BASE}/predict/slippage`, payload);
  }

  getModelStatus(): Observable<any> {
    return this.http.get<any>(`${BASE}/predict/status`);
  }

  trainModels(): Observable<any> {
    return this.http.post<any>(`${BASE}/predict/train`, {});
  }

  getRegimeStatus(): Observable<any> {
    return this.http.get<any>(`${BASE}/regime/status`);
  }

  trainRegime(): Observable<any> {
    return this.http.post<any>(`${BASE}/regime/train`, {});
  }

  getRegimeSummary(tradeDate: string): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/regime/summary`, {
      params: new HttpParams().set('trade_date', tradeDate),
    });
  }

  getRegimeDetect(tradeDate: string, sampleSize = 300): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/regime/detect`, {
      params: new HttpParams()
        .set('trade_date', tradeDate)
        .set('sample_size', String(sampleSize)),
    });
  }

  getRegimeTimeline(tradeDate: string, instrumentId: string): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/regime/timeline`, {
      params: new HttpParams()
        .set('trade_date', tradeDate)
        .set('instrument_id', instrumentId),
    });
  }

  submitFill(payload: {
    order_id: string;
    instrument_id: string;
    instrument_class: string;
    counterparty_id: string;
    side: string;
    fill_price: number;
    fill_quantity: number;
    venue_id?: string;
    market_impact_bps?: number;
    commission_bps?: number;
    currency?: string;
  }): Observable<any> {
    return this.http.post<any>(`${BASE}/fills`, payload);
  }
}
