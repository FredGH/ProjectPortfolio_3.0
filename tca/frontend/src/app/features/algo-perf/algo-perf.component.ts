import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-algo-perf',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="page">
      <h2>Algo Performance League Table</h2>
      <p class="page-intro">Ranks execution algorithms by execution quality across asset classes for a selected trade date — lower slippage and lower market impact are better.</p>
      <details class="dict-accordion">
        <summary>Column Glossary</summary>
        <table class="dict-table">
          <thead><tr><th>Column</th><th>Description</th></tr></thead>
          <tbody>
            <tr><td>Algo</td><td>Internal algorithm identifier (e.g. VWAP, TWAP, IS, POV).</td></tr>
            <tr><td>Orders</td><td>Number of orders executed by this algorithm for the selected date and asset class.</td></tr>
            <tr><td>Avg Arrival Slippage (bps)</td><td>Mean arrival slippage across all orders for this algo. Negative = cost to the fund; positive = favourable fill vs arrival price.</td></tr>
            <tr><td>Avg VWAP Slippage (bps)</td><td>Mean fill price vs the day's VWAP benchmark. Measures schedule execution quality — how well the algo tracked market volume.</td></tr>
            <tr><td>Avg Market Impact (bps)</td><td>Average estimated price impact attributed to the algorithm's own trading activity in the market.</td></tr>
            <tr><td>Participation Rate</td><td>Average fraction of market volume consumed by the algo — proxy for execution aggressiveness.</td></tr>
            <tr><td>Asset Class</td><td>Instrument class the row relates to: equity, equity_future, fixed_income, or fx_derivative.</td></tr>
          </tbody>
        </table>
      </details>

      <div class="filter-row">
        <input type="date" [(ngModel)]="tradeDate" (ngModelChange)="load()" />
        <select [(ngModel)]="instrumentClass" (ngModelChange)="load()">
          <option value="">All Asset Classes</option>
          <option value="equity">Equity</option>
          <option value="equity_future">Equity Future</option>
          <option value="fixed_income">Fixed Income</option>
          <option value="fx_derivative">FX Derivative</option>
        </select>
      </div>

      @if (loading()) {
        <p class="muted">Loading…</p>
      }

      @if (rows().length) {
        <table>
          <thead>
            <tr>
              <th>Algo</th>
              <th>Orders</th>
              <th>Avg Arrival Slippage (bps)</th>
              <th>Avg VWAP Slippage (bps)</th>
              <th>Avg Market Impact (bps)</th>
              <th>Participation Rate</th>
              <th>Asset Class</th>
            </tr>
          </thead>
          <tbody>
            @for (row of rows(); track row.algo_id) {
              <tr>
                <td>{{ row.algo_id }}</td>
                <td>{{ row.order_count | number }}</td>
                <td [class.neg]="row.avg_arrival_slippage_bps > 0">
                  {{ row.avg_arrival_slippage_bps | number:'1.2-2' }}
                </td>
                <td [class.neg]="row.avg_vwap_slippage_bps > 0">
                  {{ row.avg_vwap_slippage_bps | number:'1.2-2' }}
                </td>
                <td>{{ row.avg_market_impact_bps | number:'1.2-2' }}</td>
                <td>{{ row.avg_participation_rate | percent:'1.1-1' }}</td>
                <td>{{ row.instrument_class }}</td>
              </tr>
            }
          </tbody>
        </table>
      }

      @if (!loading() && !rows().length) {
        <p class="muted">No data for selected filters.</p>
      }
    </div>
  `,
  styles: [`
    .page { padding: 2rem; background: #0f1923; min-height: 100vh; color: #d0dde8; }
    h2 { margin-top: 0; }
    .page-intro { color: #7a8fa6; font-size: 0.88rem; margin-bottom: 1rem; margin-top: -0.25rem; max-width: 72ch; }
    details.dict-accordion { background: #131f2e; border: 1px solid #2a3f55; border-radius: 6px; margin-bottom: 1.5rem; }
    details.dict-accordion > summary { padding: 0.65rem 1rem; cursor: pointer; color: #a0b0c0; font-size: 0.82rem; font-weight: 600; list-style: none; display: flex; justify-content: space-between; align-items: center; user-select: none; }
    details.dict-accordion > summary::after { content: '▸'; font-size: 0.75rem; color: #7a8fa6; }
    details.dict-accordion[open] > summary::after { content: '▾'; }
    details.dict-accordion > summary::-webkit-details-marker { display: none; }
    .dict-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .dict-table th { text-align: left; padding: 0.5rem 1rem; color: #7a8fa6; border-bottom: 1px solid #2a3f55; font-size: 0.75rem; text-transform: uppercase; }
    .dict-table td { padding: 0.45rem 1rem; border-bottom: 1px solid #1f2f40; vertical-align: top; }
    .dict-table td:first-child { font-family: monospace; font-size: 0.78rem; color: #e0b44a; white-space: nowrap; width: 1%; padding-right: 1.5rem; }
    .dict-table tr:last-child td { border-bottom: none; }
    .filter-row { display: flex; gap: 0.75rem; margin-bottom: 1.5rem; }
    input, select { padding: 0.55rem 0.75rem; background: #1a2533;
        border: 1px solid #2a3f55; border-radius: 4px; color: #d0dde8; font-size: 0.9rem; }
    input:focus, select:focus { outline: none; border-color: #e0b44a; }
    .muted { color: #7a8fa6; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th { text-align: left; padding: 0.65rem 0.75rem; color: #7a8fa6;
        border-bottom: 1px solid #2a3f55; font-size: 0.8rem; text-transform: uppercase; }
    td { padding: 0.55rem 0.75rem; border-bottom: 1px solid #1f2f40; }
    .neg { color: #e07070; }
    tr:hover td { background: #1a2533; }
  `],
})
export class AlgoPerfComponent implements OnInit {
  private readonly api = inject(ApiService);

  tradeDate = (() => { const d = new Date(); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); })();
  instrumentClass = '';
  loading = signal(false);
  rows = signal<any[]>([]);

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.getAlgoPerformance(this.tradeDate, this.instrumentClass || undefined).subscribe({
      next: data => { this.rows.set(data); this.loading.set(false); },
      error: () => { this.rows.set([]); this.loading.set(false); },
    });
  }
}
