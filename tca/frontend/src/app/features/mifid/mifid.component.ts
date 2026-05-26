import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-mifid',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="page">
      <h2>MiFID II / RTS 27 Export</h2>
      <p class="subtitle">COMPLIANCE &amp; ADMIN access only — counterparty-scoped CSV export for regulatory transaction reporting.</p>
      <p class="page-intro">Generates the MiFID II / RTS 27 transaction report for a selected trade date. Preview the data in-browser, then download as CSV for submission to the relevant national competent authority.</p>
      <details class="dict-accordion">
        <summary>Column Glossary</summary>
        <table class="dict-table">
          <thead><tr><th>Column</th><th>Description</th></tr></thead>
          <tbody>
            <tr><td>Order ID</td><td>Unique business key for the reportable transaction.</td></tr>
            <tr><td>Instrument</td><td>Instrument identifier — ISIN or internal code.</td></tr>
            <tr><td>Class</td><td>Asset class: equity, equity_future, fixed_income, or fx_derivative.</td></tr>
            <tr><td>Side</td><td>BUY or SELL direction as reported to the trade repository.</td></tr>
            <tr><td>Quantity</td><td>Total executed quantity in shares, contracts, or nominal units.</td></tr>
            <tr><td>Notional (€M)</td><td>Gross notional value of the transaction in millions of euros (avg fill price × quantity).</td></tr>
            <tr><td>Venue</td><td>Execution venue MIC code (ISO 10383) where the transaction was executed.</td></tr>
            <tr><td>Waiver</td><td>Pre-trade transparency waiver type applied under MiFIR Art. 4 — LRGS (large-in-scale), SIZE (above standard size), ILQD (illiquid instrument), or blank.</td></tr>
            <tr><td>LRGS Deferral</td><td>Whether post-trade publication was deferred under the Large-in-Scale regime (MiFIR Art. 11).</td></tr>
            <tr><td>RTS 27 Cat.</td><td>Execution quality category per RTS 27 reporting requirements (e.g. Lit, Dark, OTC, SI).</td></tr>
            <tr><td>Trade Date</td><td>Calendar date on which the transaction was executed.</td></tr>
          </tbody>
        </table>
      </details>

      <div class="filter-row">
        <input type="date" [(ngModel)]="tradeDate" />
        <button (click)="preview()" [disabled]="loading()">Preview</button>
        <button class="export-btn" (click)="exportCsv()" [disabled]="!rows().length || loading()">
          Export CSV
        </button>
      </div>

      @if (loading()) {
        <p class="muted">Loading…</p>
      }

      @if (rows().length) {
        <p class="row-count">{{ rows().length }} records</p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Order ID</th>
                <th>Instrument</th>
                <th>Class</th>
                <th>Side</th>
                <th>Quantity</th>
                <th>Notional (€M)</th>
                <th>Venue</th>
                <th>Waiver</th>
                <th>LRGS Deferral</th>
                <th>RTS 27 Cat.</th>
                <th>Trade Date</th>
              </tr>
            </thead>
            <tbody>
              @for (row of rows().slice(0, 100); track row.order_id) {
                <tr>
                  <td>{{ row.order_id }}</td>
                  <td>{{ row.instrument_id }}</td>
                  <td>{{ row.instrument_class }}</td>
                  <td>{{ row.side }}</td>
                  <td>{{ row.total_quantity | number }}</td>
                  <td>{{ (row.notional_eur / 1_000_000) | number:'1.2-2' }}</td>
                  <td>{{ row.venue_id }}</td>
                  <td>{{ row.waiver_type || '—' }}</td>
                  <td>{{ row.is_lrgs_deferral ? 'Yes' : 'No' }}</td>
                  <td>{{ row.rts27_category || '—' }}</td>
                  <td>{{ row.trade_date }}</td>
                </tr>
              }
            </tbody>
          </table>
          @if (rows().length > 100) {
            <p class="muted">Showing first 100 of {{ rows().length }} — use Export CSV for full dataset.</p>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .page { padding: 2rem; background: #0f1923; min-height: 100vh; color: #d0dde8; }
    h2 { margin-top: 0; }
    .subtitle { color: #7a8fa6; margin-top: -0.5rem; margin-bottom: 0.5rem; font-size: 0.85rem; }
    .page-intro { color: #7a8fa6; font-size: 0.88rem; margin-bottom: 1rem; max-width: 72ch; }
    details.dict-accordion { background: #131f2e; border: 1px solid #2a3f55; border-radius: 6px; margin-bottom: 1.25rem; }
    details.dict-accordion > summary { padding: 0.65rem 1rem; cursor: pointer; color: #a0b0c0; font-size: 0.82rem; font-weight: 600; list-style: none; display: flex; justify-content: space-between; align-items: center; user-select: none; }
    details.dict-accordion > summary::after { content: '▸'; font-size: 0.75rem; color: #7a8fa6; }
    details.dict-accordion[open] > summary::after { content: '▾'; }
    details.dict-accordion > summary::-webkit-details-marker { display: none; }
    .dict-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .dict-table th { text-align: left; padding: 0.5rem 1rem; color: #7a8fa6; border-bottom: 1px solid #2a3f55; font-size: 0.75rem; text-transform: uppercase; }
    .dict-table td { padding: 0.45rem 1rem; border-bottom: 1px solid #1f2f40; vertical-align: top; }
    .dict-table td:first-child { font-family: monospace; font-size: 0.78rem; color: #e0b44a; white-space: nowrap; width: 1%; padding-right: 1.5rem; }
    .dict-table tr:last-child td { border-bottom: none; }
    .filter-row { display: flex; gap: 0.75rem; margin-bottom: 1rem; align-items: center; }
    input { padding: 0.55rem 0.75rem; background: #1a2533;
        border: 1px solid #2a3f55; border-radius: 4px; color: #d0dde8; font-size: 0.9rem; }
    button { padding: 0.55rem 1.1rem; background: #1a2533; border: 1px solid #2a3f55;
        border-radius: 4px; color: #d0dde8; cursor: pointer; font-size: 0.9rem; }
    button:hover:not(:disabled) { border-color: #e0b44a; color: #e0b44a; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .export-btn { background: #e0b44a; border-color: #e0b44a; color: #0f1923; font-weight: 600; }
    .export-btn:hover:not(:disabled) { background: #c9a03e; border-color: #c9a03e; color: #0f1923; }
    .muted { color: #7a8fa6; font-size: 0.85rem; }
    .row-count { color: #a0b0c0; font-size: 0.85rem; margin-bottom: 0.5rem; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; white-space: nowrap; }
    th { text-align: left; padding: 0.6rem 0.75rem; color: #7a8fa6;
        border-bottom: 1px solid #2a3f55; font-size: 0.78rem; text-transform: uppercase; }
    td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #1f2f40; }
  `],
})
export class MifidComponent {
  private readonly api = inject(ApiService);

  tradeDate = new Date().toISOString().slice(0, 10);
  loading = signal(false);
  rows = signal<any[]>([]);

  preview(): void {
    this.loading.set(true);
    this.api.getMifidExport(this.tradeDate).subscribe({
      next: data => { this.rows.set(data); this.loading.set(false); },
      error: () => { this.rows.set([]); this.loading.set(false); },
    });
  }

  exportCsv(): void {
    if (!this.rows().length) return;
    const headers = Object.keys(this.rows()[0]).join(',');
    const body = this.rows()
      .map(r => Object.values(r).map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
      .join('\n');
    const blob = new Blob([headers + '\n' + body], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `mifid_rts27_${this.tradeDate}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }
}
