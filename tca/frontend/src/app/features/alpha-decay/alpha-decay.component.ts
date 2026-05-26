import { Component, inject, signal, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-alpha-decay',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="page">
      <h2>Alpha Decay by Volatility Regime</h2>
      <p class="page-intro">Measures how quickly post-trade alpha (excess return vs arrival price) erodes across time horizons, segmented by volatility regime — helps calibrate optimal execution urgency.</p>
      <details class="dict-accordion">
        <summary>Column Glossary</summary>
        <table class="dict-table">
          <thead><tr><th>Column</th><th>Description</th></tr></thead>
          <tbody>
            <tr><td>Volatility Regime</td><td>LOW / MEDIUM / HIGH — classified by Z-score of the 30-day realised volatility at the time of order submission.</td></tr>
            <tr><td>Instrument Class</td><td>Asset class: equity, equity_future, fixed_income, or fx_derivative.</td></tr>
            <tr><td>Arrival vs T+30m (bps)</td><td>Average alpha (market return vs arrival price) measured 30 minutes after order submission. Negative = adverse move (price moved against the order).</td></tr>
            <tr><td>Arrival vs T+1h (bps)</td><td>Same metric at 1 hour post-submission — shows the rate of alpha decay.</td></tr>
            <tr><td>Arrival vs T+4h (bps)</td><td>Alpha measured 4 hours after submission. Convergence towards zero indicates mean reversion; persistence indicates genuine trend.</td></tr>
            <tr><td>Orders</td><td>Number of orders used to compute the averages for this regime / asset class combination.</td></tr>
          </tbody>
        </table>
      </details>

      <div class="filter-row">
        <input type="date" [(ngModel)]="tradeDate" (ngModelChange)="load()" />
      </div>

      @if (loading()) {
        <p class="muted">Loading…</p>
      }

      @for (regime of regimes; track regime) {
        @if (byRegime()[regime]?.length) {
          <section class="regime-section">
            <h3 [class]="'regime-' + regime.toLowerCase()">
              {{ regime }} Volatility Regime
            </h3>
            <table>
              <thead>
                <tr>
                  <th>Instrument Class</th>
                  <th>Arrival vs T+30m (bps)</th>
                  <th>Arrival vs T+1h (bps)</th>
                  <th>Arrival vs T+4h (bps)</th>
                  <th>Orders</th>
                </tr>
              </thead>
              <tbody>
                @for (row of byRegime()[regime]; track row.instrument_class) {
                  <tr>
                    <td>{{ row.instrument_class }}</td>
                    <td [class.neg]="row.alpha_t30m_bps > 0">
                      {{ row.alpha_t30m_bps | number:'1.2-2' }}
                    </td>
                    <td [class.neg]="row.alpha_t1h_bps > 0">
                      {{ row.alpha_t1h_bps | number:'1.2-2' }}
                    </td>
                    <td [class.neg]="row.alpha_t4h_bps > 0">
                      {{ row.alpha_t4h_bps | number:'1.2-2' }}
                    </td>
                    <td>{{ row.order_count }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </section>
        }
      }

      @if (!loading() && !hasData()) {
        <p class="muted">No alpha decay data for selected date.</p>
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
    .filter-row { margin-bottom: 1.5rem; }
    input { padding: 0.55rem 0.75rem; background: #1a2533;
        border: 1px solid #2a3f55; border-radius: 4px; color: #d0dde8; font-size: 0.9rem; }
    input:focus { outline: none; border-color: #e0b44a; }
    .muted { color: #7a8fa6; }
    .regime-section { margin-bottom: 2rem; }
    h3 { margin: 0 0 0.75rem; font-size: 0.95rem; }
    .regime-low { color: #40a070; }
    .regime-medium { color: #e0a040; }
    .regime-high { color: #e07070; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th { text-align: left; padding: 0.6rem 0.75rem; color: #7a8fa6;
        border-bottom: 1px solid #2a3f55; font-size: 0.8rem; text-transform: uppercase; }
    td { padding: 0.55rem 0.75rem; border-bottom: 1px solid #1f2f40; }
    .neg { color: #e07070; }
  `],
})
export class AlphaDecayComponent implements OnInit {
  private readonly api = inject(ApiService);

  tradeDate = (() => {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
  })();
  loading = signal(false);
  rows = signal<any[]>([]);

  readonly regimes = ['LOW', 'MEDIUM', 'HIGH'];

  readonly byRegime = computed(() => {
    const out: Partial<Record<string, any[]>> = {};
    for (const row of this.rows()) {
      const r = row.vol_regime as string;
      if (!out[r]) out[r] = [];
      out[r]!.push(row);
    }
    return out;
  });

  hasData(): boolean {
    return this.rows().length > 0;
  }

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.getAlphaDecay(this.tradeDate).subscribe({
      next: data => { this.rows.set(data); this.loading.set(false); },
      error: () => { this.rows.set([]); this.loading.set(false); },
    });
  }
}
