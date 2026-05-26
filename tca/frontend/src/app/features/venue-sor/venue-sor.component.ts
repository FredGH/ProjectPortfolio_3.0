import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/services/api.service';

interface VenueRow {
  venue_id: string;
  instrument_class: string;
  order_count: number;
  avg_slippage_vs_vwap_bps: number;
  avg_market_impact_bps: number;
  fill_rate: number;
  avg_spread_bps: number;
  rank: number;
}

@Component({
  selector: 'app-venue-sor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="page">
      <h2>Venue / SOR Scorecard</h2>
      <p class="page-intro">Ranks execution venues and Smart Order Router destinations by execution quality — lower VWAP slippage and higher fill rate indicate better venue selection.</p>
      <details class="dict-accordion">
        <summary>Column Glossary</summary>
        <table class="dict-table">
          <thead><tr><th>Column</th><th>Description</th></tr></thead>
          <tbody>
            <tr><td>Rank</td><td>Venue ranking by average VWAP slippage (1 = best, i.e. lowest cost). Highlighted row = top-ranked venue.</td></tr>
            <tr><td>Venue</td><td>Execution venue identifier — MIC code (ISO 10383) or internal broker code.</td></tr>
            <tr><td>Asset Class</td><td>Instrument class the row relates to: equity, equity_future, fixed_income, or fx_derivative.</td></tr>
            <tr><td>Orders</td><td>Number of orders routed to this venue for the selected date and asset class filter.</td></tr>
            <tr><td>Slippage vs VWAP (bps)</td><td>Mean fill price vs the day's VWAP — primary ranking metric. Negative = favourable (better than market average).</td></tr>
            <tr><td>Market Impact (bps)</td><td>Average estimated price impact attributable to orders placed at this venue.</td></tr>
            <tr><td>Fill Rate</td><td>Proportion of order quantity filled at this venue — higher is better.</td></tr>
            <tr><td>Avg Spread (bps)</td><td>Average bid/ask spread observed at the venue at order submission time — proxy for liquidity quality.</td></tr>
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
              <th>Rank</th>
              <th>Venue</th>
              <th>Asset Class</th>
              <th>Orders</th>
              <th>Slippage vs VWAP (bps)</th>
              <th>Market Impact (bps)</th>
              <th>Fill Rate</th>
              <th>Avg Spread (bps)</th>
            </tr>
          </thead>
          <tbody>
            @for (row of rows(); track row.venue_id + row.instrument_class) {
              <tr [class.top]="row.rank === 1">
                <td class="rank">{{ row.rank }}</td>
                <td>{{ row.venue_id }}</td>
                <td>{{ row.instrument_class }}</td>
                <td>{{ row.order_count | number }}</td>
                <td [class.neg]="row.avg_slippage_vs_vwap_bps > 0">
                  {{ row.avg_slippage_vs_vwap_bps | number:'1.2-2' }}
                </td>
                <td>{{ row.avg_market_impact_bps | number:'1.2-2' }}</td>
                <td>{{ row.fill_rate | percent:'1.1-1' }}</td>
                <td>{{ row.avg_spread_bps | number:'1.2-2' }}</td>
              </tr>
            }
          </tbody>
        </table>
      }

      @if (!loading() && !rows().length) {
        <p class="muted">No venue data for selected filters.</p>
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
    th { text-align: left; padding: 0.6rem 0.75rem; color: #7a8fa6;
        border-bottom: 1px solid #2a3f55; font-size: 0.8rem; text-transform: uppercase; }
    td { padding: 0.55rem 0.75rem; border-bottom: 1px solid #1f2f40; }
    .neg { color: #e07070; }
    .rank { font-weight: 700; color: #e0b44a; }
    tr.top td { background: #1e2f1a; }
    tr:hover td { background: #1a2533; }
  `],
})
export class VenueSorComponent implements OnInit {
  private readonly api = inject(ApiService);

  tradeDate = (() => { const d = new Date(); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); })();
  instrumentClass = '';
  loading = signal(false);
  rows = signal<VenueRow[]>([]);

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.getTcaSummary(this.tradeDate, undefined, this.instrumentClass || undefined).subscribe({
      next: (data: any) => {
        const venueMap = new Map<string, VenueRow>();
        for (const row of data) {
          const key = `${row.venue_id}__${row.instrument_class}`;
          if (!venueMap.has(key)) {
            venueMap.set(key, {
              venue_id: row.venue_id, instrument_class: row.instrument_class,
              order_count: 0, avg_slippage_vs_vwap_bps: 0,
              avg_market_impact_bps: 0, fill_rate: 0, avg_spread_bps: 0, rank: 0,
            });
          }
          const v = venueMap.get(key)!;
          v.order_count++;
          v.avg_slippage_vs_vwap_bps += row.vwap_slippage_bps ?? 0;
          v.avg_market_impact_bps += row.market_impact_bps ?? 0;
          v.fill_rate += 1;
          v.avg_spread_bps += row.spread_bps ?? 0;
        }
        const venues = Array.from(venueMap.values()).map(v => ({
          ...v,
          avg_slippage_vs_vwap_bps: v.avg_slippage_vs_vwap_bps / v.order_count,
          avg_market_impact_bps: v.avg_market_impact_bps / v.order_count,
          fill_rate: v.fill_rate / v.order_count,
          avg_spread_bps: v.avg_spread_bps / v.order_count,
        }));
        venues.sort((a, b) => a.avg_slippage_vs_vwap_bps - b.avg_slippage_vs_vwap_bps);
        venues.forEach((v, i) => v.rank = i + 1);
        this.rows.set(venues);
        this.loading.set(false);
      },
      error: () => { this.rows.set([]); this.loading.set(false); },
    });
  }
}
