import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/auth/auth.service';
import { Store } from '@ngrx/store';
import { AuthActions } from '../../store/auth.actions';

@Component({
  selector: 'app-client-view',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  template: `
    <div class="layout">
      <nav class="sidebar">
        <div class="brand">PrivateBank TCA</div>
        <div class="client-info">
          <span class="label">Counterparty</span>
          <span class="value">{{ counterpartyId() }}</span>
        </div>
        <ul class="client-nav">
          <li><a routerLink="/glossary" routerLinkActive="active">TCA Glossary</a></li>
        </ul>
        <button class="logout-btn" (click)="logout()">Sign Out</button>
      </nav>

      <main class="content">
        <div class="header">
          <h2>My Orders — {{ today }}</h2>
          <div class="filter-row">
            <input type="date" [(ngModel)]="tradeDate" (ngModelChange)="load()" />
          </div>
        </div>

        <p class="page-intro">Self-service order history scoped to your counterparty — execution quality for your own orders only. No cross-counterparty data is accessible.</p>
        <details class="dict-accordion">
          <summary>Column Glossary</summary>
          <table class="dict-table">
            <thead><tr><th>Column</th><th>Description</th></tr></thead>
            <tbody>
              <tr><td>Order ID</td><td>Unique reference for the order — use this when querying your prime broker or custodian for reconciliation.</td></tr>
              <tr><td>Instrument</td><td>Bloomberg ticker or ISIN of the security traded on your behalf.</td></tr>
              <tr><td>Class</td><td>Asset class: equity, equity_future, fixed_income, or fx_derivative.</td></tr>
              <tr><td>Side</td><td>BUY or SELL direction of the order.</td></tr>
              <tr><td>Quantity</td><td>Total order quantity in shares, contracts, or nominal units.</td></tr>
              <tr><td>Avg Fill (€)</td><td>Volume-weighted average execution price across all fills, in euros.</td></tr>
              <tr><td>Slippage (bps)</td><td>Arrival slippage: (avg fill − arrival price) / arrival price × 10,000. Red = cost relative to arrival price.</td></tr>
              <tr><td>Algo</td><td>Execution algorithm used by PrivateBank for your order (VWAP, TWAP, IS, POV, etc.).</td></tr>
              <tr><td>Status</td><td>Current order status: FILLED, PARTIALLY_FILLED, CANCELLED, or PENDING.</td></tr>
              <tr><td>Avg Slippage KPI</td><td>Mean arrival slippage across all orders in the selected date — your aggregate execution cost.</td></tr>
              <tr><td>Total Notional (€M)</td><td>Sum of (avg fill × quantity) across all orders in millions of euros — total traded value.</td></tr>
            </tbody>
          </table>
        </details>

        @if (loading()) {
          <p class="muted">Loading your orders…</p>
        }

        @if (orders().length) {
          <div class="summary-row">
            <div class="kpi-card">
              <span class="kpi-label">Orders</span>
              <span class="kpi-value">{{ orders().length }}</span>
            </div>
            <div class="kpi-card">
              <span class="kpi-label">Avg Slippage</span>
              <span class="kpi-value">{{ avgSlippage() | number:'1.1-1' }} bps</span>
            </div>
            <div class="kpi-card">
              <span class="kpi-label">Total Notional (€M)</span>
              <span class="kpi-value">{{ totalNotional() | number:'1.1-1' }}</span>
            </div>
          </div>

          <table>
            <thead>
              <tr>
                <th>Order ID</th>
                <th>Instrument</th>
                <th>Class</th>
                <th>Side</th>
                <th>Quantity</th>
                <th>Avg Fill (€)</th>
                <th>Slippage (bps)</th>
                <th>Algo</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              @for (order of orders(); track order.order_id) {
                <tr>
                  <td class="mono">{{ order.order_id }}</td>
                  <td>{{ order.instrument_id }}</td>
                  <td>{{ order.instrument_class }}</td>
                  <td>{{ order.side }}</td>
                  <td>{{ order.total_quantity | number }}</td>
                  <td>{{ order.avg_fill_price | number:'1.4-4' }}</td>
                  <td [class.neg]="order.arrival_slippage_bps > 0">
                    {{ order.arrival_slippage_bps | number:'1.2-2' }}
                  </td>
                  <td>{{ order.algo_id }}</td>
                  <td>{{ order.order_status }}</td>
                </tr>
              }
            </tbody>
          </table>
        }

        @if (!loading() && !orders().length) {
          <p class="muted">No orders found for {{ tradeDate }}.</p>
        }
      </main>
    </div>
  `,
  styles: [`
    .layout { display: flex; height: 100vh; background: #0f1923; color: #d0dde8; }
    .sidebar { width: 220px; background: #1a2533; border-right: 1px solid #2a3f55;
        display: flex; flex-direction: column; padding: 1.5rem 1rem; }
    .brand { color: #e0b44a; font-size: 1.1rem; font-weight: 700; margin-bottom: 1.5rem; }
    .client-info { padding: 0.75rem; background: #243347; border-radius: 4px; margin-bottom: auto; }
    .client-info .label { display: block; color: #7a8fa6; font-size: 0.75rem; }
    .client-info .value { display: block; color: #d0dde8; font-weight: 600; font-size: 0.9rem; }
    .client-nav { list-style: none; margin: 1rem 0 0; padding: 0; }
    .client-nav li { margin-bottom: 0.25rem; }
    .client-nav a { display: block; padding: 0.4rem 0.6rem; border-radius: 4px;
        color: #7a8fa6; text-decoration: none; font-size: 0.85rem; }
    .client-nav a:hover, .client-nav a.active { background: #243347; color: #e0b44a; }
    .logout-btn { width: 100%; padding: 0.5rem; background: transparent; margin-top: 1rem;
        border: 1px solid #2a3f55; border-radius: 4px; color: #7a8fa6;
        cursor: pointer; font-size: 0.85rem; }
    .logout-btn:hover { border-color: #e07070; color: #e07070; }
    .content { flex: 1; overflow-y: auto; padding: 2rem; }
    .header { margin-bottom: 1rem; }
    h2 { margin: 0 0 0.75rem; }
    .filter-row { display: flex; gap: 0.75rem; }
    .page-intro { color: #7a8fa6; font-size: 0.88rem; margin-bottom: 1rem; max-width: 72ch; }
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
    input { padding: 0.5rem 0.75rem; background: #1a2533;
        border: 1px solid #2a3f55; border-radius: 4px; color: #d0dde8; font-size: 0.9rem; }
    input:focus { outline: none; border-color: #e0b44a; }
    .muted { color: #7a8fa6; }
    .summary-row { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
    .kpi-card { background: #1a2533; border: 1px solid #2a3f55; border-radius: 6px;
        padding: 1rem 1.25rem; min-width: 140px; }
    .kpi-label { display: block; color: #7a8fa6; font-size: 0.78rem; margin-bottom: 0.4rem; }
    .kpi-value { font-size: 1.4rem; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th { text-align: left; padding: 0.6rem 0.75rem; color: #7a8fa6;
        border-bottom: 1px solid #2a3f55; font-size: 0.8rem; text-transform: uppercase; }
    td { padding: 0.55rem 0.75rem; border-bottom: 1px solid #1f2f40; }
    .neg { color: #e07070; }
    .mono { font-family: monospace; font-size: 0.8rem; color: #a0b0c0; }
  `],
})
export class ClientViewComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly authService = inject(AuthService);
  private readonly store = inject(Store);

  today = new Date().toISOString().slice(0, 10);
  yesterday = (() => { const d = new Date(); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); })();
  tradeDate = this.yesterday;
  loading = signal(false);
  orders = signal<any[]>([]);

  counterpartyId(): string {
    return this.authService.currentUser()?.counterparty_id ?? '';
  }

  avgSlippage(): number {
    const data = this.orders();
    if (!data.length) return 0;
    return data.reduce((s: number, o: any) => s + (o.arrival_slippage_bps ?? 0), 0) / data.length;
  }

  totalNotional(): number {
    return this.orders().reduce(
      (s: number, o: any) => s + ((o.avg_fill_price ?? 0) * (o.total_quantity ?? 0)) / 1_000_000,
      0,
    );
  }

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.getOrders(this.tradeDate).subscribe({
      next: data => { this.orders.set(data); this.loading.set(false); },
      error: () => { this.orders.set([]); this.loading.set(false); },
    });
  }

  logout(): void {
    this.store.dispatch(AuthActions.logout());
  }
}
