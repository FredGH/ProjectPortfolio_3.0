import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-order-tca',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="page">
      <h2>Order TCA</h2>
      <p class="page-intro">Per-order transaction cost analysis — browse all orders for a given trade date, filter by asset class, and click any row to view a full cost decomposition and MiFID II fields.</p>
      <details class="dict-accordion">
        <summary>Column Glossary</summary>
        <table class="dict-table">
          <thead><tr><th colspan="2">List columns</th></tr></thead>
          <tbody>
            <tr><td>Order ID</td><td>Unique business key for the order (first 8 chars shown).</td></tr>
            <tr><td>Instrument</td><td>Bloomberg ticker or internal instrument code.</td></tr>
            <tr><td>Class</td><td>Asset class: equity, equity_future, fixed_income, or fx_derivative.</td></tr>
            <tr><td>Side</td><td>BUY (green) or SELL (red) direction of the order.</td></tr>
            <tr><td>Qty</td><td>Order quantity in shares, contracts, or nominal units.</td></tr>
            <tr><td>Slippage (bps)</td><td>Arrival slippage: (avg fill − arrival price) / arrival price × 10,000. Negative = good (filled better than arrival). Positive = cost. Red = adverse.</td></tr>
            <tr><td>Quality</td><td>Execution quality rating — Excellent / Good / Fair / Poor — derived from slippage percentile within the instrument class.</td></tr>
            <tr><td>Algo</td><td>Execution algorithm used (VWAP, TWAP, IS, POV, etc.).</td></tr>
          </tbody>
          <thead><tr><th colspan="2">Detail panel — click any row to open</th></tr></thead>
          <tbody>
            <tr><td>Arrival Price</td><td>Mid-price of the instrument at the moment the order was submitted to the market.</td></tr>
            <tr><td>Avg Fill</td><td>Volume-weighted average execution price across all fills for this order.</td></tr>
            <tr><td>Market Impact (bps)</td><td>Price movement attributable to the order's own footprint in the market.</td></tr>
            <tr><td>Commission (bps)</td><td>Broker commission expressed in basis points of notional traded.</td></tr>
            <tr><td>Total Cost (bps)</td><td>Sum of arrival slippage + market impact + commission — all-in transaction cost.</td></tr>
            <tr><td>VWAP Slippage (bps)</td><td>Avg fill vs the day's VWAP. Negative = good (beat the market average). Positive = cost (underperformed passive participation).</td></tr>
            <tr><td>Vol Regime</td><td>Volatility regime at order time: LOW / MEDIUM / HIGH (Z-score of 30-day realised vol).</td></tr>
            <tr><td>Venue</td><td>Primary execution venue (MIC code or internal broker code).</td></tr>
            <tr><td>Trader</td><td>Trader ID responsible for the order.</td></tr>
            <tr><td>Waiver</td><td>MiFID II pre-trade transparency waiver type applied (LRGS, SIZE, ILQD, or blank if none).</td></tr>
            <tr><td>Deferral</td><td>Post-trade publication deferral type granted under MiFID II Art. 11.</td></tr>
            <tr><td>OTC</td><td>Whether the order was executed off-exchange (over the counter).</td></tr>
            <tr><td>SI Flag</td><td>Systematic Internaliser flag — counterparty is acting as SI under MiFID II.</td></tr>
            <tr><td>Settlement</td><td>Expected settlement date for the transaction.</td></tr>
          </tbody>
        </table>
      </details>

      <div class="toolbar">
        <input type="date" [(ngModel)]="tradeDate" (ngModelChange)="loadList()" />
        <select [(ngModel)]="filterClass" (ngModelChange)="loadList()">
          <option value="">All asset classes</option>
          <option value="equity">Equity</option>
          <option value="equity_future">Equity Future</option>
          <option value="fixed_income">Fixed Income</option>
          <option value="fx_derivative">FX Derivative</option>
        </select>
        <span class="count">{{ orders().length }} orders</span>
      </div>

      <div class="split" [class.has-detail]="detail()">

        <!-- Order list -->
        <div class="list-panel">
          @if (listLoading()) {
            <p class="muted">Loading…</p>
          } @else if (!orders().length) {
            <p class="muted">No orders for selected date.</p>
          } @else {
            <table>
              <thead>
                <tr>
                  <th>Order ID</th>
                  <th>Instrument</th>
                  <th>Class</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Slippage (bps)</th>
                  <th>Quality</th>
                  <th>Algo</th>
                </tr>
              </thead>
              <tbody>
                @for (o of orders(); track o.order_id) {
                  <tr
                    (click)="selectOrder(o)"
                    [class.selected]="selectedId() === o.order_id"
                  >
                    <td class="mono">{{ o.order_id | slice:0:8 }}…</td>
                    <td>{{ o.instrument_id }}</td>
                    <td><span class="chip chip-{{ o.instrument_class }}">{{ o.instrument_class }}</span></td>
                    <td [class.buy]="o.side === 'BUY'" [class.sell]="o.side === 'SELL'">{{ o.side }}</td>
                    <td>{{ o.quantity | number }}</td>
                    <td [class.neg]="o.arrival_slippage_bps > 0">
                      {{ o.arrival_slippage_bps != null ? (o.arrival_slippage_bps | number:'1.2-2') : '—' }}
                    </td>
                    <td><span class="chip chip-q-{{ (o.execution_quality || '') | lowercase }}">{{ o.execution_quality || '—' }}</span></td>
                    <td>{{ o.algo_id || '—' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </div>

        <!-- Detail panel -->
        @if (detail()) {
          <div class="detail-panel">
            <div class="detail-header">
              <span class="mono">{{ detail().order_id }}</span>
              <button class="close-btn" (click)="detail.set(null)">✕</button>
            </div>

            @if (detailLoading()) {
              <p class="muted">Loading detail…</p>
            } @else {
              <div class="detail-grid">
                <div class="detail-card">
                  <h3>Execution</h3>
                  <dl>
                    <dt>Instrument</dt><dd>{{ detail().instrument_id }}</dd>
                    <dt>Side</dt><dd>{{ detail().side }}</dd>
                    <dt>Quantity</dt><dd>{{ detail().quantity | number }}</dd>
                    <dt>Arrival Price</dt><dd>{{ detail().arrival_price | number:'1.4-4' }}</dd>
                    <dt>Avg Fill</dt><dd>{{ detail().avg_fill_price | number:'1.4-4' }}</dd>
                  </dl>
                </div>
                <div class="detail-card">
                  <h3>Cost (bps)</h3>
                  <dl>
                    <dt>Arrival Slippage</dt>
                    <dd [class.neg]="detail().arrival_slippage_bps > 0">{{ detail().arrival_slippage_bps | number:'1.2-2' }}</dd>
                    <dt>Market Impact</dt><dd>{{ detail().market_impact_bps | number:'1.2-2' }}</dd>
                    <dt>Commission</dt><dd>{{ detail().commission_bps | number:'1.2-2' }}</dd>
                    <dt>Total Cost</dt><dd>{{ detail().total_cost_bps | number:'1.2-2' }}</dd>
                    <dt>VWAP Slippage</dt><dd>{{ detail().vwap_slippage_bps | number:'1.2-2' }}</dd>
                  </dl>
                </div>
                <div class="detail-card">
                  <h3>Metadata</h3>
                  <dl>
                    <dt>Algo</dt><dd>{{ detail().algo_id }}</dd>
                    <dt>Venue</dt><dd>{{ detail().venue_id }}</dd>
                    <dt>Trader</dt><dd>{{ detail().trader_id }}</dd>
                    <dt>Vol Regime</dt><dd>{{ detail().vol_regime }}</dd>
                    <dt>Quality</dt><dd>{{ detail().execution_quality }}</dd>
                  </dl>
                </div>
                <div class="detail-card">
                  <h3>MiFID II</h3>
                  <dl>
                    <dt>Waiver</dt><dd>{{ detail().pre_trade_waiver_type || '—' }}</dd>
                    <dt>Deferral</dt><dd>{{ detail().post_trade_deferral_type || '—' }}</dd>
                    <dt>OTC</dt><dd>{{ detail().is_otc ? 'Yes' : 'No' }}</dd>
                    <dt>SI Flag</dt><dd>{{ detail().si_flag ? 'Yes' : 'No' }}</dd>
                    <dt>Settlement</dt><dd>{{ detail().settlement_date | date:'dd MMM' }}</dd>
                  </dl>
                </div>
              </div>
            }
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    .page { padding: 2rem; background: #0f1923; min-height: 100vh; color: #d0dde8;
        display: flex; flex-direction: column; }
    h2 { margin-top: 0; }
    .page-intro { color: #7a8fa6; font-size: 0.88rem; margin-bottom: 1rem; margin-top: -0.25rem; max-width: 72ch; }
    details.dict-accordion { background: #131f2e; border: 1px solid #2a3f55; border-radius: 6px; margin-bottom: 1.25rem; }
    details.dict-accordion > summary { padding: 0.65rem 1rem; cursor: pointer; color: #a0b0c0; font-size: 0.82rem; font-weight: 600; list-style: none; display: flex; justify-content: space-between; align-items: center; user-select: none; }
    details.dict-accordion > summary::after { content: '▸'; font-size: 0.75rem; color: #7a8fa6; }
    details.dict-accordion[open] > summary::after { content: '▾'; }
    details.dict-accordion > summary::-webkit-details-marker { display: none; }
    .dict-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .dict-table th { text-align: left; padding: 0.5rem 1rem; color: #4a6070; border-bottom: 1px solid #2a3f55; border-top: 1px solid #2a3f55; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; background: #0f1923; }
    .dict-table thead:first-child th { border-top: none; }
    .dict-table td { padding: 0.45rem 1rem; border-bottom: 1px solid #1f2f40; vertical-align: top; }
    .dict-table td:first-child { font-family: monospace; font-size: 0.78rem; color: #e0b44a; white-space: nowrap; width: 1%; padding-right: 1.5rem; }
    .dict-table tr:last-child td { border-bottom: none; }
    .toolbar { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; }
    input[type=date], select { padding: 0.5rem 0.75rem; background: #1a2533;
        border: 1px solid #2a3f55; border-radius: 4px; color: #d0dde8; font-size: 0.9rem; }
    input[type=date]:focus, select:focus { outline: none; border-color: #e0b44a; }
    .count { color: #7a8fa6; font-size: 0.85rem; margin-left: auto; }
    .muted { color: #7a8fa6; }

    .split { display: flex; gap: 1rem; flex: 1; min-height: 0; overflow: hidden; }
    .list-panel { flex: 1; overflow-y: auto; }
    .has-detail .list-panel { flex: 0 0 55%; }

    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    th { position: sticky; top: 0; background: #0f1923; text-align: left;
        padding: 0.55rem 0.6rem; color: #7a8fa6; border-bottom: 1px solid #2a3f55;
        font-size: 0.75rem; text-transform: uppercase; z-index: 1; }
    td { padding: 0.45rem 0.6rem; border-bottom: 1px solid #1f2f40; }
    tr:hover td { background: #1a2533; cursor: pointer; }
    tr.selected td { background: #1e3050; }
    .mono { font-family: monospace; font-size: 0.78rem; }
    .buy { color: #40a070; font-weight: 600; }
    .sell { color: #e07070; font-weight: 600; }
    .neg { color: #e07070; }

    .chip { padding: 0.15rem 0.45rem; border-radius: 10px; font-size: 0.72rem;
        font-weight: 600; background: #243347; color: #a0b0c0; }
    .chip-equity { background: #1a3a2a; color: #40c080; }
    .chip-equity_future { background: #1a2a3a; color: #4090e0; }
    .chip-fixed_income { background: #3a2a1a; color: #e09040; }
    .chip-fx_derivative { background: #2a1a3a; color: #a060e0; }
    .chip-q-excellent { background: #1a3a2a; color: #40c080; }
    .chip-q-good { background: #243347; color: #a0b0c0; }
    .chip-q-fair { background: #3a2a1a; color: #e09040; }
    .chip-q-poor { background: #3a1a1a; color: #e07070; }

    .detail-panel { flex: 0 0 43%; background: #131f2e; border: 1px solid #2a3f55;
        border-radius: 6px; overflow-y: auto; padding: 1rem; }
    .detail-header { display: flex; justify-content: space-between; align-items: center;
        margin-bottom: 1rem; }
    .close-btn { background: transparent; border: none; color: #7a8fa6;
        font-size: 1rem; cursor: pointer; padding: 0.25rem 0.5rem; }
    .close-btn:hover { color: #e07070; }
    .detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
    .detail-card { background: #1a2533; border: 1px solid #2a3f55; border-radius: 5px; padding: 0.85rem; }
    h3 { margin: 0 0 0.65rem; color: #7a8fa6; font-size: 0.75rem; text-transform: uppercase; }
    dl { display: grid; grid-template-columns: 1fr 1fr; gap: 0.2rem 0; margin: 0; }
    dt { color: #7a8fa6; font-size: 0.78rem; padding: 0.2rem 0; }
    dd { font-size: 0.82rem; margin: 0; padding: 0.2rem 0; text-align: right; }
  `],
})
export class OrderTcaComponent implements OnInit {
  private readonly api = inject(ApiService);

  tradeDate = (() => { const d = new Date(); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); })();
  filterClass = '';

  orders = signal<any[]>([]);
  listLoading = signal(false);
  selectedId = signal<string | null>(null);
  detail = signal<any>(null);
  detailLoading = signal(false);

  ngOnInit(): void {
    this.loadList();
  }

  loadList(): void {
    this.listLoading.set(true);
    this.detail.set(null);
    this.selectedId.set(null);
    this.api.getTcaSummary(this.tradeDate, undefined, this.filterClass || undefined).subscribe({
      next: data => { this.orders.set(data); this.listLoading.set(false); },
      error: () => { this.orders.set([]); this.listLoading.set(false); },
    });
  }

  selectOrder(o: any): void {
    this.selectedId.set(o.order_id);
    this.detail.set(o);
    this.detailLoading.set(true);
    this.api.getOrderTca(o.order_id).subscribe({
      next: data => { this.detail.set(data); this.detailLoading.set(false); },
      error: () => { this.detailLoading.set(false); },
    });
  }
}
