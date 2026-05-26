import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'app-submit-fill',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="page">
      <h2>Submit Fill</h2>
      <p class="page-intro">Record an execution against an open order. Select the order, enter the fill details, and submit. The fill is written immediately to the real-time fills table.</p>

      <!-- Order picker -->
      <div class="card">
        <h3>1 — Select order</h3>
        <div class="row">
          <label>Trade date</label>
          <input type="date" [(ngModel)]="tradeDate" (ngModelChange)="loadOrders()" />
        </div>
        <div class="row">
          <label>Order</label>
          <select [(ngModel)]="selectedOrder" (ngModelChange)="onOrderSelected()">
            <option [ngValue]="null">— pick an order —</option>
            @for (o of orders(); track o.order_id) {
              <option [ngValue]="o">{{ o.order_id | slice:0:12 }}… {{ o.instrument_id }} {{ o.side }} {{ o.quantity | number }}</option>
            }
          </select>
        </div>

        @if (selectedOrder) {
          <div class="order-summary">
            <span class="badge badge-{{ selectedOrder.instrument_class }}">{{ selectedOrder.instrument_class }}</span>
            <span [class.buy]="selectedOrder.side === 'BUY'" [class.sell]="selectedOrder.side === 'SELL'">{{ selectedOrder.side }}</span>
            <span>{{ selectedOrder.quantity | number }} &#64; arrival {{ selectedOrder.arrival_price | number:'1.4-4' }}</span>
            <span class="muted">Counterparty: {{ selectedOrder.counterparty_id }}</span>
          </div>
        }
      </div>

      <!-- Fill details -->
      @if (selectedOrder) {
        <div class="card">
          <h3>2 — Fill details</h3>

          <div class="form-grid">
            <div class="field">
              <label>Fill Price <span class="req">*</span></label>
              <input type="number" step="0.0001" [(ngModel)]="form.fill_price" placeholder="e.g. 50.1250" />
              @if (selectedOrder) {
                <span class="hint">Arrival was {{ selectedOrder.arrival_price | number:'1.4-4' }}</span>
              }
            </div>

            <div class="field">
              <label>Fill Quantity <span class="req">*</span></label>
              <input type="number" step="1" [(ngModel)]="form.fill_quantity" placeholder="e.g. 5000" />
              @if (selectedOrder) {
                <span class="hint">Order qty: {{ selectedOrder.quantity | number }}</span>
              }
            </div>

            <div class="field">
              <label>Venue</label>
              <select [(ngModel)]="form.venue_id">
                <option value="">— select —</option>
                <option value="XLON">XLON — London Stock Exchange</option>
                <option value="XETR">XETR — XETRA (Deutsche Börse)</option>
                <option value="XPAR">XPAR — Euronext Paris</option>
                <option value="XAMS">XAMS — Euronext Amsterdam</option>
                <option value="XCSE">XCSE — Copenhagen</option>
                <option value="XSTO">XSTO — Stockholm</option>
                <option value="XHEL">XHEL — Helsinki</option>
                <option value="BATE">BATE — CBOE Europe</option>
                <option value="CHIX">CHIX — Chi-X</option>
                <option value="TRQX">TRQX — Turquoise</option>
                <option value="DARK">DARK — Dark Pool (internal)</option>
                <option value="OTC">OTC — Over the Counter</option>
              </select>
            </div>

            <div class="field">
              <label>Currency</label>
              <select [(ngModel)]="form.currency">
                <option value="EUR">EUR</option>
                <option value="GBP">GBP</option>
                <option value="USD">USD</option>
                <option value="DKK">DKK</option>
                <option value="SEK">SEK</option>
                <option value="NOK">NOK</option>
              </select>
            </div>

            <div class="field">
              <label>Market Impact (bps)</label>
              <input type="number" step="0.01" [(ngModel)]="form.market_impact_bps" placeholder="optional" />
            </div>

            <div class="field">
              <label>Commission (bps)</label>
              <input type="number" step="0.01" [(ngModel)]="form.commission_bps" placeholder="e.g. 7.5" />
            </div>
          </div>

          <!-- Implied slippage preview -->
          @if (form.fill_price && selectedOrder) {
            <div class="preview">
              <span class="preview-label">Implied arrival slippage</span>
              <span [class.neg]="impliedSlippage() < 0" [class.pos]="impliedSlippage() > 0">
                {{ impliedSlippage() | number:'1.2-2' }} bps
              </span>
              <span class="muted">({{ selectedOrder.side === 'BUY' ? 'negative = cost' : 'positive = cost' }})</span>
            </div>
          }

          <div class="actions">
            <button class="submit-btn" (click)="submit()" [disabled]="submitting() || !canSubmit()">
              {{ submitting() ? 'Submitting…' : 'Submit Fill' }}
            </button>
            <button class="reset-btn" (click)="reset()">Reset</button>
          </div>
        </div>
      }

      <!-- Result -->
      @if (result()) {
        <div class="result success">
          <strong>Fill recorded</strong>
          <div class="result-grid">
            <span>Fill ID</span><span class="mono">{{ result().fill_id }}</span>
            <span>Order</span><span class="mono">{{ result().order_id | slice:0:12 }}…</span>
            <span>Price</span><span>{{ result().fill_price | number:'1.4-4' }}</span>
            <span>Quantity</span><span>{{ result().fill_quantity | number }}</span>
            <span>Time</span><span>{{ result().fill_time | date:'HH:mm:ss' }} UTC</span>
          </div>
        </div>
      }

      @if (error()) {
        <div class="result error">
          <strong>Submission failed</strong> — {{ error() }}
        </div>
      }
    </div>
  `,
  styles: [`
    .page { padding: 2rem; background: #0f1923; min-height: 100vh; color: #d0dde8; max-width: 900px; }
    h2 { margin-top: 0; }
    .page-intro { color: #7a8fa6; font-size: 0.88rem; margin-bottom: 1.5rem; max-width: 72ch; }
    .card { background: #131f2e; border: 1px solid #2a3f55; border-radius: 6px; padding: 1.25rem; margin-bottom: 1.25rem; }
    h3 { margin: 0 0 1rem; color: #a0b0c0; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .row { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.75rem; }
    .row label { color: #7a8fa6; font-size: 0.85rem; min-width: 80px; }
    input[type=date], input[type=number], select {
      padding: 0.5rem 0.75rem; background: #1a2533; border: 1px solid #2a3f55;
      border-radius: 4px; color: #d0dde8; font-size: 0.9rem; }
    input[type=date]:focus, input[type=number]:focus, select:focus { outline: none; border-color: #e0b44a; }
    select { min-width: 220px; }
    .order-summary { display: flex; gap: 1.25rem; align-items: center; font-size: 0.85rem;
        background: #1a2533; border-radius: 4px; padding: 0.6rem 0.85rem; margin-top: 0.5rem; }
    .muted { color: #7a8fa6; }
    .buy { color: #40a070; font-weight: 600; }
    .sell { color: #e07070; font-weight: 600; }
    .badge { padding: 0.15rem 0.45rem; border-radius: 10px; font-size: 0.72rem; font-weight: 600; background: #243347; color: #a0b0c0; }
    .badge-equity { background: #1a3a2a; color: #40c080; }
    .badge-equity_future { background: #1a2a3a; color: #4090e0; }
    .badge-fixed_income { background: #3a2a1a; color: #e09040; }
    .badge-fx_derivative { background: #2a1a3a; color: #a060e0; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem 1.5rem; margin-bottom: 1rem; }
    .field { display: flex; flex-direction: column; gap: 0.3rem; }
    .field label { color: #7a8fa6; font-size: 0.82rem; }
    .field input, .field select { width: 100%; box-sizing: border-box; }
    .req { color: #e07070; }
    .hint { color: #7a8fa6; font-size: 0.76rem; }
    .preview { display: flex; align-items: center; gap: 0.75rem; font-size: 0.85rem;
        background: #1a2533; border-radius: 4px; padding: 0.6rem 0.85rem; margin-bottom: 1rem; }
    .preview-label { color: #7a8fa6; }
    .neg { color: #e07070; font-weight: 700; }
    .pos { color: #40a070; font-weight: 700; }
    .actions { display: flex; gap: 0.75rem; }
    .submit-btn { padding: 0.6rem 1.5rem; background: #e0b44a; border: none; border-radius: 4px;
        color: #0f1923; font-weight: 700; cursor: pointer; font-size: 0.9rem; }
    .submit-btn:hover:not(:disabled) { background: #c9a03e; }
    .submit-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .reset-btn { padding: 0.6rem 1.1rem; background: transparent; border: 1px solid #2a3f55;
        border-radius: 4px; color: #7a8fa6; cursor: pointer; font-size: 0.9rem; }
    .reset-btn:hover { border-color: #a0b0c0; color: #d0dde8; }
    .result { border-radius: 6px; padding: 1rem 1.25rem; margin-top: 1rem; }
    .result.success { background: #1a3a2a; border: 1px solid #2a6040; }
    .result.error { background: #3a1a1a; border: 1px solid #6a2a2a; color: #e07070; }
    .result-grid { display: grid; grid-template-columns: max-content 1fr; gap: 0.3rem 1rem;
        margin-top: 0.6rem; font-size: 0.85rem; }
    .result-grid span:nth-child(odd) { color: #7a8fa6; }
    .mono { font-family: monospace; font-size: 0.82rem; }
  `],
})
export class SubmitFillComponent implements OnInit {
  private readonly api = inject(ApiService);

  tradeDate = (() => {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
  })();
  orders = signal<any[]>([]);
  selectedOrder: any = null;
  submitting = signal(false);
  result = signal<any>(null);
  error = signal<string | null>(null);

  form = {
    fill_price: null as number | null,
    fill_quantity: null as number | null,
    venue_id: '',
    currency: 'EUR',
    market_impact_bps: null as number | null,
    commission_bps: null as number | null,
  };

  ngOnInit(): void {
    this.loadOrders();
  }

  loadOrders(): void {
    this.selectedOrder = null;
    this.api.getTcaSummary(this.tradeDate).subscribe({
      next: data => this.orders.set(data),
      error: () => this.orders.set([]),
    });
  }

  onOrderSelected(): void {
    this.result.set(null);
    this.error.set(null);
    if (this.selectedOrder) {
      this.form.fill_quantity = this.selectedOrder.quantity;
      this.form.fill_price = this.selectedOrder.arrival_price;
    }
  }

  impliedSlippage(): number {
    if (!this.form.fill_price || !this.selectedOrder?.arrival_price) return 0;
    const dir = this.selectedOrder.side === 'BUY' ? 1 : -1;
    return dir * ((this.form.fill_price - this.selectedOrder.arrival_price) / this.selectedOrder.arrival_price) * 10000;
  }

  canSubmit(): boolean {
    return !!this.selectedOrder && !!this.form.fill_price && !!this.form.fill_quantity;
  }

  submit(): void {
    if (!this.canSubmit()) return;
    this.submitting.set(true);
    this.result.set(null);
    this.error.set(null);

    const o = this.selectedOrder;
    this.api.submitFill({
      order_id: o.order_id,
      instrument_id: o.instrument_id,
      instrument_class: o.instrument_class,
      counterparty_id: o.counterparty_id,
      side: o.side,
      fill_price: this.form.fill_price!,
      fill_quantity: this.form.fill_quantity!,
      venue_id: this.form.venue_id || undefined,
      market_impact_bps: this.form.market_impact_bps ?? undefined,
      commission_bps: this.form.commission_bps ?? undefined,
      currency: this.form.currency,
    }).subscribe({
      next: data => {
        this.result.set(data);
        this.submitting.set(false);
        this.selectedOrder = null;
        this.form = { fill_price: null, fill_quantity: null, venue_id: '', currency: 'EUR', market_impact_bps: null, commission_bps: null };
      },
      error: err => {
        this.error.set(err?.error?.detail ?? 'Unknown error');
        this.submitting.set(false);
      },
    });
  }

  reset(): void {
    this.selectedOrder = null;
    this.result.set(null);
    this.error.set(null);
    this.form = { fill_price: null, fill_quantity: null, venue_id: '', currency: 'EUR', market_impact_bps: null, commission_bps: null };
  }
}
