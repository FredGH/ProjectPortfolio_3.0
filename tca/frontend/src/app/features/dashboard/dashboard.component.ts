import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { Store } from '@ngrx/store';
import { AuthService } from '../../core/auth/auth.service';
import { ApiService } from '../../core/services/api.service';
import { AuthActions } from '../../store/auth.actions';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterModule],
  template: `
    <div class="layout">
      <nav class="sidebar">
        <div class="brand">PrivateBank TCA</div>
        <ul>
          <li><a routerLink="/dashboard" routerLinkActive="active">Dashboard</a></li>
          @if (canAccessTrader()) {
            <li><a routerLink="/submit-fill" routerLinkActive="active">Submit Fill</a></li>
          }

          <li class="nav-group">Reports</li>
          <li><a routerLink="/order-tca" routerLinkActive="active">Order TCA</a></li>
          @if (canAccessTrader()) {
            <li><a routerLink="/algo-perf" routerLinkActive="active">Algo Performance</a></li>
            <li><a routerLink="/alpha-decay" routerLinkActive="active">Alpha Decay</a></li>
            <li><a routerLink="/venue-sor" routerLinkActive="active">Venue / SOR</a></li>
          }
          @if (canAccessCompliance()) {
            <li><a routerLink="/mifid" routerLinkActive="active">MiFID Export</a></li>
          }

          @if (canAccessTrader()) {
            <li class="nav-group">ML Predictions</li>
            <li><a routerLink="/pre-trade" routerLinkActive="active">Pre-Trade Estimate</a></li>
            <li><a routerLink="/regime-detection" routerLinkActive="active">Regime Detection</a></li>
          }

          @if (isClient()) {
            <li><a routerLink="/client-view" routerLinkActive="active">My Orders</a></li>
          }
          <li class="nav-divider"></li>
          <li><a routerLink="/glossary" routerLinkActive="active">TCA Glossary</a></li>
        </ul>
        <button class="logout-btn" (click)="logout()">Sign Out</button>
      </nav>

      <main class="content">
        <div class="header">
          <h2>Overview — {{ today }}</h2>
          <span class="role-badge role-{{ role() | lowercase }}">{{ role() }}</span>
        </div>

        <p class="page-intro">Operational overview for the current trading session — KPI summary, data quality alerts, and quick navigation to detailed analysis modules.</p>
        <details class="dict-accordion">
          <summary>Column Glossary</summary>
          <table class="dict-table">
            <thead><tr><th>Column</th><th>Description</th></tr></thead>
            <tbody>
              <tr><td>Warnings</td><td>Number of active data quality alerts raised by the anomaly detector since midnight.</td></tr>
              <tr><td>Orders today</td><td>Total orders across all counterparties for today's trade date.</td></tr>
              <tr><td>Avg Slippage</td><td>Mean arrival slippage in basis points across all orders today. Negative = favourable (filled better than arrival price).</td></tr>
              <tr><td>Trade Date</td><td>Reference date for all KPIs on this page (today's calendar date).</td></tr>
              <tr><td>Table</td><td>Database table that triggered the warning (e.g. fact_order_execution).</td></tr>
              <tr><td>Check</td><td>Name of the dbt-expectations or custom anomaly check that failed.</td></tr>
              <tr><td>Rows</td><td>Number of rows affected by the detected anomaly.</td></tr>
              <tr><td>Value</td><td>The anomalous value or threshold breach that triggered the warning.</td></tr>
            </tbody>
          </table>
        </details>

        <div class="kpi-grid">
          <div class="kpi-card">
            <span class="kpi-label">Warnings</span>
            <span class="kpi-value warn">{{ warningCount() }}</span>
          </div>
          <div class="kpi-card">
            <span class="kpi-label">Orders today</span>
            <span class="kpi-value">{{ orders().length }}</span>
          </div>
          <div class="kpi-card">
            <span class="kpi-label">Avg Slippage</span>
            <span class="kpi-value">{{ avgSlippage() | number:'1.1-1' }} bps</span>
          </div>
          <div class="kpi-card">
            <span class="kpi-label">Trade Date</span>
            <span class="kpi-value">{{ today }}</span>
          </div>
        </div>

        @if (warnings().length) {
          <section class="warnings-section">
            <h3>Active Warnings</h3>
            <table>
              <thead><tr><th>Table</th><th>Check</th><th>Rows</th><th>Value</th></tr></thead>
              <tbody>
                @for (w of warnings(); track w.id) {
                  <tr>
                    <td>{{ w.affected_table || '—' }}</td>
                    <td>{{ w.check_name || '—' }}</td>
                    <td>{{ w.affected_rows ?? '—' }}</td>
                    <td>{{ w.warn_value || '—' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </section>
        }
      </main>
    </div>
  `,
  styles: [`
    .layout { display: flex; height: 100vh; background: #0f1923; color: #d0dde8; }
    .sidebar {
      width: 220px; background: #1a2533; border-right: 1px solid #2a3f55;
      display: flex; flex-direction: column; padding: 1.5rem 1rem;
    }
    .brand { color: #e0b44a; font-size: 1.1rem; font-weight: 700; margin-bottom: 2rem; }
    ul { list-style: none; margin: 0; padding: 0; flex: 1; }
    li { margin-bottom: 0.35rem; }
    a { display: block; padding: 0.5rem 0.75rem; border-radius: 4px;
        color: #a0b0c0; text-decoration: none; font-size: 0.9rem; }
    a.active, a:hover { background: #243347; color: #e0b44a; }
    .nav-group { padding: 1rem 0.75rem 0.2rem; color: #4a6070; font-size: 0.68rem;
        font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; }
    .nav-divider { margin: 0.75rem 0.75rem 0.35rem; border-top: 1px solid #1f2f40; }
    .logout-btn { width: 100%; padding: 0.5rem; background: transparent;
        border: 1px solid #2a3f55; border-radius: 4px; color: #7a8fa6;
        cursor: pointer; font-size: 0.85rem; }
    .logout-btn:hover { border-color: #e07070; color: #e07070; }
    .content { flex: 1; overflow-y: auto; padding: 2rem; }
    .header { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem; }
    h2 { margin: 0; font-size: 1.4rem; }
    .role-badge { padding: 0.25rem 0.65rem; border-radius: 12px; font-size: 0.75rem;
        font-weight: 600; background: #243347; color: #e0b44a; text-transform: uppercase; }
    .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 2rem; }
    .kpi-card { background: #1a2533; border: 1px solid #2a3f55; border-radius: 6px; padding: 1.25rem; }
    .kpi-label { display: block; color: #7a8fa6; font-size: 0.8rem; margin-bottom: 0.5rem; }
    .kpi-value { font-size: 1.6rem; font-weight: 700; }
    .kpi-value.warn { color: #e0a040; }
    .warnings-section h3 { margin-top: 0; color: #a0b0c0; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th { text-align: left; padding: 0.6rem 0.75rem; color: #7a8fa6;
        border-bottom: 1px solid #2a3f55; }
    td { padding: 0.55rem 0.75rem; border-bottom: 1px solid #1f2f40; }
    .sev-high td { color: #e07070; }
    .sev-medium td { color: #e0a040; }
    .page-intro { color: #7a8fa6; font-size: 0.88rem; margin-bottom: 1rem; margin-top: -0.5rem; max-width: 72ch; }
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
  `],
})
export class DashboardComponent implements OnInit {
  private readonly store = inject(Store);
  private readonly authService = inject(AuthService);
  private readonly api = inject(ApiService);

  today = new Date().toISOString().slice(0, 10);

  orders = signal<any[]>([]);
  warnings = signal<any[]>([]);
  warningCount = signal(0);
  avgSlippage = signal(0);

  canAccessTrader(): boolean {
    return this.authService.hasRole('TRADER', 'HEAD_OF_TRADING', 'COMPLIANCE', 'ADMIN');
  }

  canAccessCompliance(): boolean {
    return this.authService.hasRole('COMPLIANCE', 'ADMIN');
  }

  isClient(): boolean {
    return this.authService.hasRole('CLIENT');
  }

  role(): string {
    return this.authService.currentUser()?.role ?? '';
  }

  ngOnInit(): void {
    this.api.getOrders(this.today).subscribe({
      next: data => {
        this.orders.set(data);
        if (data.length) {
          const sum = data.reduce((acc: number, o: any) => acc + (o.arrival_slippage_bps ?? 0), 0);
          this.avgSlippage.set(sum / data.length);
        }
      },
    });

    this.api.getWarnings().subscribe({
      next: data => {
        this.warnings.set(data.slice(0, 10));
        this.warningCount.set(data.length);
      },
    });
  }

  logout(): void {
    this.store.dispatch(AuthActions.logout());
  }
}
