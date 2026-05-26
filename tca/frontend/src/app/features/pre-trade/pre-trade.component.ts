import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/auth/auth.service';

@Component({
  selector: 'app-pre-trade',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="page">
      <h2>Pre-Trade Slippage Estimate</h2>
      <p class="page-intro">Enter the parameters of a planned order to get a predicted arrival slippage before execution. The model is trained on historical fills from <code>fact_order_execution</code>.</p>

      <!-- Model status -->
      <div class="status-bar">
        @for (cls of classes; track cls) {
          <div class="status-chip" [class.ready]="modelStatus()[cls]?.ready" [class.not-ready]="!modelStatus()[cls]?.ready">
            <span class="dot"></span>
            {{ cls }}
            @if (modelStatus()[cls]?.ready) {
              <span class="samples">({{ modelStatus()[cls].trained_on | number }} rows)</span>
            } @else {
              <span class="samples">not trained</span>
            }
          </div>
        }
        @if (isAdmin()) {
          <button class="train-btn" (click)="trainModels()" [disabled]="training()">
            {{ training() ? 'Training…' : 'Train / Retrain Models' }}
          </button>
        }
      </div>

      @if (trainResult()) {
        <div class="train-result">
          @for (entry of trainResultEntries(); track entry.cls) {
            <span class="train-chip" [class.ok]="entry.status === 'trained'">
              {{ entry.cls }}: {{ entry.status === 'trained' ? (entry.cv_r2_mean | number:'1.3-3') + ' R²' : entry.status }}
            </span>
          }
        </div>
      }

      <!-- Input form -->
      <div class="card">
        <div class="form-grid">
          <div class="field">
            <label>Instrument Class <span class="req">*</span></label>
            <select [(ngModel)]="form.instrument_class">
              <option value="equity">Equity</option>
              <option value="equity_future">Equity Future</option>
              <option value="fixed_income">Fixed Income</option>
              <option value="fx_derivative">FX Derivative</option>
            </select>
          </div>

          <div class="field">
            <label>Side <span class="req">*</span></label>
            <select [(ngModel)]="form.side">
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </div>

          <div class="field">
            <label>Quantity <span class="req">*</span></label>
            <input type="number" step="100" [(ngModel)]="form.quantity" placeholder="e.g. 10000" />
          </div>

          <div class="field">
            <label>Volatility Regime</label>
            <select [(ngModel)]="form.vol_regime">
              <option value="LOW">LOW</option>
              <option value="MEDIUM">MEDIUM</option>
              <option value="HIGH">HIGH</option>
            </select>
          </div>

          <div class="field">
            <label>Algorithm</label>
            <select [(ngModel)]="form.algo_id">
              <option value="">Unknown / not yet decided</option>
              <option value="VWAP">VWAP</option>
              <option value="TWAP">TWAP</option>
              <option value="IS">IS (Implementation Shortfall)</option>
              <option value="POV">POV</option>
              <option value="SNIPER">SNIPER</option>
              <option value="ARRIVAL">ARRIVAL</option>
            </select>
          </div>

          <div class="field">
            <label>Venue</label>
            <select [(ngModel)]="form.venue_id">
              <option value="">Unknown / SOR decides</option>
              <option value="XLON">XLON</option>
              <option value="XETR">XETR</option>
              <option value="XPAR">XPAR</option>
              <option value="XAMS">XAMS</option>
              <option value="BATE">BATE</option>
              <option value="CHIX">CHIX</option>
              <option value="TRQX">TRQX</option>
            </select>
          </div>

          <div class="field">
            <label>Planned execution hour (CET)</label>
            <input type="number" min="7" max="17" [(ngModel)]="form.order_hour" />
            <span class="hint">European session: 8–17</span>
          </div>

          <div class="field">
            <label>Day of week</label>
            <select [(ngModel)]="form.order_dow">
              <option [value]="1">Monday</option>
              <option [value]="2">Tuesday</option>
              <option [value]="3">Wednesday</option>
              <option [value]="4">Thursday</option>
              <option [value]="5">Friday</option>
            </select>
          </div>
        </div>

        <button class="predict-btn" (click)="predict()" [disabled]="predicting() || !form.quantity">
          {{ predicting() ? 'Estimating…' : 'Get Estimate' }}
        </button>
      </div>

      <!-- Result -->
      @if (result()) {
        <div class="result-card">
          <div class="result-headline">
            <span class="result-label">Predicted Arrival Slippage</span>
            <span class="result-value" [class.neg]="result().predicted_slippage_bps < 0" [class.pos]="result().predicted_slippage_bps >= 0">
              {{ result().predicted_slippage_bps | number:'1.2-2' }} bps
            </span>
          </div>

          <div class="result-ci">
            <span class="ci-label">Confidence interval (IQR)</span>
            <span>{{ result().ci_low_bps | number:'1.2-2' }} to {{ result().ci_high_bps | number:'1.2-2' }} bps</span>
          </div>

          <div class="result-interpretation">
            @if (result().predicted_slippage_bps < -5) {
              <span class="badge warn">High cost expected — consider splitting the order or using IS with a faster schedule.</span>
            } @else if (result().predicted_slippage_bps < 0) {
              <span class="badge caution">Moderate cost — standard VWAP/TWAP should be adequate.</span>
            } @else {
              <span class="badge ok">Favourable fill expected — passive execution (VWAP/POV) is appropriate.</span>
            }
          </div>

          <div class="importance-section">
            <span class="imp-label">Top cost drivers</span>
            <div class="importance-bars">
              @for (f of topFeatures(); track f.name) {
                <div class="imp-row">
                  <span class="imp-name">{{ f.name }}</span>
                  <div class="imp-bar-wrap">
                    <div class="imp-bar" [style.width.%]="f.pct"></div>
                  </div>
                  <span class="imp-val">{{ f.pct | number:'1.1-1' }}%</span>
                </div>
              }
            </div>
          </div>

          <p class="trained-on">Model trained on {{ result().trained_on | number }} historical orders.</p>
        </div>
      }

      @if (error()) {
        <div class="error-box">{{ error() }}</div>
      }
    </div>
  `,
  styles: [`
    .page { padding: 2rem; background: #0f1923; min-height: 100vh; color: #d0dde8; max-width: 900px; }
    h2 { margin-top: 0; }
    .page-intro { color: #7a8fa6; font-size: 0.88rem; margin-bottom: 1.25rem; max-width: 72ch; }
    code { font-family: monospace; font-size: 0.82rem; color: #e0b44a; }

    .status-bar { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-bottom: 1.25rem; }
    .status-chip { display: flex; align-items: center; gap: 0.4rem; padding: 0.3rem 0.7rem;
        border-radius: 12px; font-size: 0.78rem; background: #1a2533; border: 1px solid #2a3f55; }
    .status-chip.ready { border-color: #2a6040; }
    .status-chip.not-ready { opacity: 0.6; }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: #3a3a3a; }
    .ready .dot { background: #40c080; }
    .samples { color: #7a8fa6; font-size: 0.72rem; }
    .train-btn { margin-left: auto; padding: 0.4rem 0.9rem; background: #243347;
        border: 1px solid #2a3f55; border-radius: 4px; color: #d0dde8; cursor: pointer; font-size: 0.82rem; }
    .train-btn:hover:not(:disabled) { border-color: #e0b44a; color: #e0b44a; }
    .train-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    .train-result { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 1rem; }
    .train-chip { padding: 0.25rem 0.6rem; border-radius: 4px; font-size: 0.78rem;
        background: #1a2533; border: 1px solid #2a3f55; color: #7a8fa6; }
    .train-chip.ok { border-color: #2a6040; color: #40c080; }

    .card { background: #131f2e; border: 1px solid #2a3f55; border-radius: 6px; padding: 1.25rem; margin-bottom: 1.25rem; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem 1.5rem; margin-bottom: 1.25rem; }
    .field { display: flex; flex-direction: column; gap: 0.3rem; }
    .field label { color: #7a8fa6; font-size: 0.82rem; }
    .field input, .field select { padding: 0.5rem 0.7rem; background: #1a2533;
        border: 1px solid #2a3f55; border-radius: 4px; color: #d0dde8; font-size: 0.9rem; width: 100%; box-sizing: border-box; }
    .field input:focus, .field select:focus { outline: none; border-color: #e0b44a; }
    .hint { color: #7a8fa6; font-size: 0.75rem; }
    .req { color: #e07070; }

    .predict-btn { padding: 0.65rem 2rem; background: #e0b44a; border: none; border-radius: 4px;
        color: #0f1923; font-weight: 700; cursor: pointer; font-size: 0.95rem; }
    .predict-btn:hover:not(:disabled) { background: #c9a03e; }
    .predict-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    .result-card { background: #131f2e; border: 1px solid #2a5040; border-radius: 6px; padding: 1.5rem; }
    .result-headline { display: flex; align-items: baseline; gap: 1rem; margin-bottom: 0.75rem; }
    .result-label { color: #7a8fa6; font-size: 0.9rem; }
    .result-value { font-size: 2.2rem; font-weight: 700; }
    .neg { color: #e07070; }
    .pos { color: #40a070; }
    .result-ci { font-size: 0.83rem; color: #7a8fa6; margin-bottom: 0.75rem; }
    .ci-label { color: #a0b0c0; margin-right: 0.5rem; }
    .result-interpretation { margin-bottom: 1rem; }
    .badge { display: inline-block; padding: 0.3rem 0.75rem; border-radius: 4px; font-size: 0.82rem; }
    .badge.warn { background: #3a1a1a; color: #e07070; border: 1px solid #6a2a2a; }
    .badge.caution { background: #3a2a1a; color: #e0a040; border: 1px solid #6a4a1a; }
    .badge.ok { background: #1a3a2a; color: #40c080; border: 1px solid #2a6040; }

    .importance-section { margin-top: 1rem; }
    .imp-label { font-size: 0.78rem; color: #7a8fa6; text-transform: uppercase; letter-spacing: 0.05em; display: block; margin-bottom: 0.5rem; }
    .importance-bars { display: flex; flex-direction: column; gap: 0.4rem; }
    .imp-row { display: grid; grid-template-columns: 120px 1fr 45px; gap: 0.5rem; align-items: center; font-size: 0.8rem; }
    .imp-name { color: #a0b0c0; }
    .imp-bar-wrap { background: #1a2533; border-radius: 2px; height: 6px; }
    .imp-bar { background: #e0b44a; height: 6px; border-radius: 2px; }
    .imp-val { color: #7a8fa6; text-align: right; }
    .trained-on { font-size: 0.76rem; color: #7a8fa6; margin: 0.75rem 0 0; }
    .error-box { background: #3a1a1a; border: 1px solid #6a2a2a; border-radius: 6px;
        padding: 1rem; color: #e07070; font-size: 0.85rem; }
  `],
})
export class PreTradeComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  readonly classes = ['equity', 'equity_future', 'fixed_income', 'fx_derivative'];

  form = {
    instrument_class: 'equity',
    side: 'BUY',
    quantity: null as number | null,
    vol_regime: 'MEDIUM',
    algo_id: '',
    venue_id: '',
    order_hour: 10,
    order_dow: 2,
  };

  modelStatus = signal<Record<string, any>>({});
  predicting = signal(false);
  training = signal(false);
  result = signal<any>(null);
  trainResult = signal<any>(null);
  error = signal<string | null>(null);

  isAdmin(): boolean {
    return this.auth.hasRole('ADMIN');
  }

  ngOnInit(): void {
    this.refreshStatus();
  }

  refreshStatus(): void {
    this.api.getModelStatus().subscribe({
      next: data => this.modelStatus.set(data),
      error: () => {},
    });
  }

  trainModels(): void {
    this.training.set(true);
    this.trainResult.set(null);
    this.api.trainModels().subscribe({
      next: data => {
        this.trainResult.set(data);
        this.training.set(false);
        this.refreshStatus();
      },
      error: err => {
        this.error.set(err?.error?.detail ?? 'Training failed');
        this.training.set(false);
      },
    });
  }

  predict(): void {
    if (!this.form.quantity) return;
    this.predicting.set(true);
    this.result.set(null);
    this.error.set(null);

    this.api.predictSlippage({
      instrument_class: this.form.instrument_class,
      side: this.form.side,
      quantity: this.form.quantity,
      vol_regime: this.form.vol_regime,
      algo_id: this.form.algo_id || undefined,
      venue_id: this.form.venue_id || undefined,
      order_hour: this.form.order_hour,
      order_dow: this.form.order_dow,
    }).subscribe({
      next: data => { this.result.set(data); this.predicting.set(false); },
      error: err => {
        this.error.set(err?.error?.detail ?? 'Prediction failed — model may not be trained yet.');
        this.predicting.set(false);
      },
    });
  }

  topFeatures(): { name: string; pct: number }[] {
    const imp = this.result()?.feature_importance;
    if (!imp) return [];
    const total = Object.values(imp as Record<string, number>).reduce((a, b) => a + b, 0);
    return Object.entries(imp as Record<string, number>)
      .map(([name, val]) => ({ name, pct: (val / total) * 100 }))
      .sort((a, b) => b.pct - a.pct)
      .slice(0, 5);
  }

  trainResultEntries(): { cls: string; status: string; cv_r2_mean?: number }[] {
    const r = this.trainResult();
    if (!r?.models) return [];
    return Object.entries(r.models as Record<string, any>).map(([cls, v]) => ({
      cls,
      status: v.status,
      cv_r2_mean: v.cv_r2_mean,
    }));
  }
}
