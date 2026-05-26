import { Component, inject, signal, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { forkJoin } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/auth/auth.service';

interface RegimeSummary {
  regime: 'LOW' | 'MEDIUM' | 'HIGH';
  description: string;
  tick_count: number;
  pct_of_session: number;
  avg_intraday_vol: number;
  avg_volume_ratio: number;
  avg_momentum: number;
  avg_confidence: number;
}

interface ScatterPoint {
  instrument_id: string;
  regime: string;
  intraday_vol: number;
  volume_ratio: number;
  momentum: number;
  cluster_confidence: number;
}

interface TimelineBar {
  ts: string;
  regime: string;
  confidence: number;
}

@Component({
  selector: 'app-regime-detection',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="page">

      <!-- ── Header ─────────────────────────────────────────── -->
      <div class="page-header">
        <div>
          <h2>ML Regime Detection <span class="ml-badge">Unsupervised · K-Means</span></h2>
          <p class="page-intro">
            Identifies intraday market regimes from 30-second OHLCV bars using unsupervised clustering
            on three microstructure features: bar price-range (volatility), normalised volume, and
            directional momentum. Replaces the legacy threshold-based vol regime with a data-driven
            classifier — the ML regime label enriches every other model in the TCA platform.
          </p>
        </div>
      </div>

      <!-- ── Glossary ────────────────────────────────────────── -->
      <details class="dict-accordion">
        <summary>Feature & Column Glossary</summary>
        <table class="dict-table">
          <thead><tr><th>Name</th><th>Description</th></tr></thead>
          <tbody>
            <tr><td>intraday_vol</td><td>(high − low) / close — normalised bar price range; primary volatility proxy from OHLCV bars.</td></tr>
            <tr><td>volume_ratio</td><td>Z-scored bar volume within each instrument × day. Positive = above-average liquidity/urgency; negative = quiet tape.</td></tr>
            <tr><td>momentum</td><td>(close − open) / open — signed directional return per bar. Positive = buying pressure; negative = selling pressure.</td></tr>
            <tr><td>cluster_confidence</td><td>Inverse normalised distance to nearest cluster centroid. Higher = the bar is a cleaner member of its regime.</td></tr>
            <tr><td>LOW regime</td><td>Tight spread, low volume variability, minimal directional pressure — trending or quiet session. Passive strategies (VWAP/TWAP) preferred.</td></tr>
            <tr><td>MEDIUM regime</td><td>Normal mixed-flow conditions — standard execution parameters apply.</td></tr>
            <tr><td>HIGH regime</td><td>Wide spread, elevated volume, strong momentum — stress or news-driven. Implementation Shortfall or faster schedule recommended.</td></tr>
          </tbody>
        </table>
      </details>

      <!-- ── TCA Business Context accordion ───────────────────── -->
      <details class="dict-accordion">
        <summary>TCA Business Context — How to Interpret This Screen</summary>
        <div class="biz-accordion-body">

          <div class="biz-section-label">Regime Execution Guide</div>
          <table class="dict-table">
            <thead>
              <tr>
                <th>Regime</th>
                <th>Market Conditions</th>
                <th>Expected TCA Cost</th>
                <th>Recommended Execution</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><span class="regime-pill pill-low">LOW</span></td>
                <td>Tight spreads, low urgency, liquid &amp; stable</td>
                <td class="cost-low">Low</td>
                <td>Passive: VWAP/TWAP, increase limit orders, avoid market orders</td>
              </tr>
              <tr>
                <td><span class="regime-pill pill-medium">MEDIUM</span></td>
                <td>Normal mixed flow, standard liquidity</td>
                <td class="cost-medium">Moderate</td>
                <td>Standard algos (VWAP / adaptive), balanced passive/aggressive</td>
              </tr>
              <tr>
                <td><span class="regime-pill pill-high">HIGH</span></td>
                <td>Wide spreads, elevated volume, stress or news-driven</td>
                <td class="cost-high">High (elevated IS)</td>
                <td>Urgency execution: IS algo, faster schedule, smaller slices, tighter risk controls</td>
              </tr>
            </tbody>
          </table>

          <div class="biz-section-label" style="margin-top:1.25rem">Understanding the Confidence Score</div>
          <table class="dict-table">
            <thead>
              <tr>
                <th>Confidence Level</th>
                <th>What it means</th>
                <th>Execution implication</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td class="conf-high">≥ 70%</td>
                <td>Bar is a clean member of its regime — label is reliable</td>
                <td>Use regime signal with confidence</td>
              </tr>
              <tr>
                <td class="conf-low">&lt; 50%</td>
                <td>Market is in a transition / blended state</td>
                <td>Regime is ambiguous — widen cost estimates, treat as unstable</td>
              </tr>
            </tbody>
          </table>

        </div>
      </details>

      <!-- ── Model status bar ───────────────────────────────── -->
      <div class="status-bar">
        <div class="status-chip" [class.ready]="modelReady()">
          <span class="dot"></span>
          @if (modelReady()) {
            K-Means trained &nbsp;·&nbsp; {{ status()?.trained_on | number }} bars &nbsp;·&nbsp;
            k={{ status()?.n_clusters }} &nbsp;·&nbsp; features: {{ status()?.features?.join(', ') }}
            &nbsp;·&nbsp; inertia {{ status()?.inertia | number:'1.1-1' }}
          } @else {
            Model not trained — click "Train" to fit the clustering model
          }
        </div>
        @if (isAdmin()) {
          <button class="train-btn" (click)="trainModel()" [disabled]="training()">
            {{ training() ? 'Training…' : 'Train / Retrain' }}
          </button>
        }
      </div>

      @if (trainResult()) {
        <div class="train-banner">
          Trained on {{ trainResult()?.trained_on | number }} bars —
          inertia {{ trainResult()?.inertia | number:'1.1-1' }} —
          {{ trainResult()?.centroids?.length }} clusters assigned
        </div>
      }

      <!-- ── Controls ───────────────────────────────────────── -->
      <div class="controls-row">
        <div class="ctrl-group">
          <label>Trade Date</label>
          <input type="date" [(ngModel)]="tradeDate" (ngModelChange)="onDateChange()" />
        </div>
        <div class="ctrl-group">
          <label>Instrument (timeline)</label>
          <select [(ngModel)]="selectedInstrument">
            <optgroup label="Equities">
              @for (i of equityInstruments; track i) {
                <option [value]="i">{{ i }}</option>
              }
            </optgroup>
            <optgroup label="Futures">
              @for (i of futureInstruments; track i) {
                <option [value]="i">{{ i }}</option>
              }
            </optgroup>
            <optgroup label="Bonds">
              @for (i of bondInstruments; track i) {
                <option [value]="i">{{ i }}</option>
              }
            </optgroup>
            <optgroup label="FX Forwards">
              @for (i of fxInstruments; track i) {
                <option [value]="i">{{ i }}</option>
              }
            </optgroup>
          </select>
        </div>
        <button class="detect-btn" (click)="runDetection()" [disabled]="detecting() || !modelReady()">
          {{ detecting() ? 'Running…' : 'Detect Regimes' }}
        </button>
      </div>

      @if (!modelReady() && !training()) {
        <div class="not-trained-box">
          <span class="nt-icon">&#9685;</span>
          <div>
            <strong>Model not trained.</strong>
            The K-Means clustering model needs to be trained before regime detection can run.
            @if (isAdmin()) {
              Click <strong>Train / Retrain</strong> above to fit the model on all available tick data.
            } @else {
              Ask an ADMIN user to train the model.
            }
          </div>
        </div>
      }

      <!-- ── Session distribution KPI cards ────────────────── -->
      @if (summary().length) {
        <div class="regime-kpi-grid">
          @for (r of summary(); track r.regime) {
            <div class="regime-card" [class]="'regime-card-' + r.regime.toLowerCase()">
              <div class="rc-header">
                <span class="rc-dot" [class]="'rc-dot-' + r.regime.toLowerCase()"></span>
                <span class="rc-label">{{ r.regime }}</span>
                <span class="rc-pct">{{ r.pct_of_session }}%</span>
              </div>
              <div class="rc-bar-wrap">
                <div class="rc-bar" [class]="'rc-bar-' + r.regime.toLowerCase()"
                     [style.width.%]="r.pct_of_session"></div>
              </div>
              <div class="rc-desc">{{ r.description }}</div>
              <div class="rc-stats">
                <div class="rc-stat">
                  <span class="rcs-label">Avg Vol</span>
                  <span class="rcs-val">{{ (r.avg_intraday_vol * 10000) | number:'1.2-2' }} bps</span>
                </div>
                <div class="rc-stat">
                  <span class="rcs-label">Volume Z</span>
                  <span class="rcs-val" [class.positive]="r.avg_volume_ratio > 0.1"
                                        [class.negative]="r.avg_volume_ratio < -0.1">
                    {{ r.avg_volume_ratio | number:'1.2-2' }}σ
                  </span>
                </div>
                <div class="rc-stat">
                  <span class="rcs-label">Momentum</span>
                  <span class="rcs-val" [class.positive]="r.avg_momentum > 0.0001"
                                        [class.negative]="r.avg_momentum < -0.0001">
                    {{ (r.avg_momentum * 10000) | number:'1.1-1' }} bps
                  </span>
                </div>
                <div class="rc-stat">
                  <span class="rcs-label">Confidence</span>
                  <span class="rcs-val">{{ (r.avg_confidence * 100) | number:'1.0-0' }}%</span>
                </div>
              </div>
              <div class="rc-ticks">{{ r.tick_count | number }} bars</div>
            </div>
          }
        </div>
      }

      @if (loadingSummary()) {
        <div class="loading-row">
          <div class="spinner"></div>
          <span class="muted">Loading regime distribution…</span>
        </div>
      }

      <!-- ── Intraday regime timeline ────────────────────────── -->
      @if (timeline().length) {
        <section class="section">
          <div class="section-header">
            <span class="section-title">Intraday Regime Timeline</span>
            <span class="section-sub">{{ selectedInstrument }} · {{ tradeDate }} · {{ timeline().length }} bars (30s each)</span>
          </div>

          <div class="how-to-read">
            <span class="htr-label">How to read</span>
            Each coloured cell represents one 30-second OHLCV bar.
            Read left→right as the trading session unfolds from open (~07:00) to close (~15:30).
            <strong class="htr-low">Green</strong> stretches are calm, low-volatility periods where passive algorithms (VWAP/TWAP) work well.
            <strong class="htr-amber">Amber</strong> is normal mixed-flow — standard execution parameters apply.
            <strong class="htr-red">Red</strong> signals stress or news-driven bursts: wider spreads, elevated volume, strong momentum — consider Implementation Shortfall or an accelerated schedule.
            Hover any cell to see the exact timestamp and confidence score.
            Long unbroken runs of one colour indicate a sustained regime; rapid colour changes signal an unstable, transitional session — widen your cost estimates.
          </div>

          <div class="timeline-legend">
            <span class="tl-chip tl-low">LOW</span>
            <span class="tl-chip tl-medium">MEDIUM</span>
            <span class="tl-chip tl-high">HIGH</span>
          </div>

          <div class="timeline-wrap">
            <div class="timeline-strip">
              @for (bar of timeline(); track $index) {
                <div class="tl-bar"
                     [class]="'tl-' + bar.regime.toLowerCase()"
                     [title]="bar.ts + ' — ' + bar.regime + ' (' + (bar.confidence * 100 | number:'1.0-0') + '% conf)'">
                </div>
              }
            </div>
            <div class="timeline-axis">
              <span>07:00</span>
              <span>09:00</span>
              <span>11:00</span>
              <span>13:00</span>
              <span>15:30</span>
            </div>
          </div>

          <div class="runs-summary">
            <span class="runs-label">Session regime mix:</span>
            @for (r of timelineRegimePcts(); track r.regime) {
              <span class="run-chip" [class]="'tl-' + r.regime.toLowerCase()">
                {{ r.regime }}: {{ r.pct | number:'1.0-0' }}%
              </span>
            }
          </div>
        </section>
      }

      <!-- ── Main analysis panels ─────────────────────────────── -->
      @if (hasDetected() || detecting()) {
        <div class="analysis-grid">

          <!-- 3D Scatter Plot (Plotly WebGL) -->
          <section class="section scatter-section">
            <div class="section-header">
              <span class="section-title">Feature Space — 3D Scatter</span>
              <span class="section-sub">
                {{ scatterPoints().length }} bars &nbsp;·&nbsp;
                drag to rotate &nbsp;·&nbsp; scroll to zoom &nbsp;·&nbsp; hover for details
              </span>
            </div>
            @if (detecting()) {
              <div class="plot-loading">
                <div class="spinner"></div>
                <span>Running detection pipeline…</span>
              </div>
            } @else if (scatterPoints().length) {
              <div id="regime-plot-3d" class="plot-3d-container"></div>
            } @else {
              <div class="no-data-box">No tick bars found for {{ tradeDate }} — try a different date.</div>
            }
            <div class="plot-3d-caption">
              x = bar price range (bps) &nbsp;·&nbsp;
              y = volume Z-score (σ) &nbsp;·&nbsp;
              z = momentum (bps) &nbsp;·&nbsp;
              color = detected regime
            </div>
          </section>

          <!-- Right: centroid comparison -->
          <section class="section centroid-section">
            <div class="section-header">
              <span class="section-title">Cluster Centroids</span>
              <span class="section-sub">Feature means per detected regime</span>
            </div>

            <div class="how-to-read">
              <span class="htr-label">How to read</span>
              Each row is the <em>centre of mass</em> of one cluster — the average feature values for all bars the model assigned to that regime.
              <strong>Price Range</strong> is the mean bar spread in basis points: a LOW centroid near 4 bps vs a HIGH centroid near 14 bps confirms the model has separated tight and wide markets.
              <strong>Volume Z</strong> is the mean z-score of bar volume within each instrument × day: positive means bars in that regime tend to trade above their daily average volume.
              <strong>Momentum</strong> is the mean directional bar return: positive = net buying pressure, negative = net selling pressure across bars in that cluster.
              <strong>Confidence</strong> is the average inverse-distance to the centroid — 90%+ means bars are tightly packed around the centre (clean cluster); below 70% suggests overlap with adjacent regimes and noisier labels.
              The wider the spread between the LOW and HIGH centroid values, the better the model separates regimes — check the <em>Regime separation</em> bars below to see which feature drives the distinction most.
            </div>

            <table class="centroid-table">
              <thead>
                <tr>
                  <th>Regime</th>
                  <th>Price Range</th>
                  <th>Volume Z</th>
                  <th>Momentum</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                @for (r of summary(); track r.regime) {
                  <tr [class]="'row-' + r.regime.toLowerCase()">
                    <td>
                      <span class="regime-pill" [class]="'pill-' + r.regime.toLowerCase()">
                        {{ r.regime }}
                      </span>
                    </td>
                    <td>{{ (r.avg_intraday_vol * 10000) | number:'1.2-2' }} bps</td>
                    <td [class.positive]="r.avg_volume_ratio > 0.05"
                        [class.negative]="r.avg_volume_ratio < -0.05">
                      {{ r.avg_volume_ratio | number:'1.3-3' }}σ
                    </td>
                    <td [class.positive]="r.avg_momentum > 0.00005"
                        [class.negative]="r.avg_momentum < -0.00005">
                      {{ (r.avg_momentum * 10000) | number:'1.2-2' }} bps
                    </td>
                    <td>{{ (r.avg_confidence * 100) | number:'1.0-0' }}%</td>
                  </tr>
                }
              </tbody>
            </table>

            <!-- Feature importance bars (relative spread of centroids) -->
            @if (featureImportance().length) {
              <div class="fi-section">
                <div class="fi-label">Regime separation by feature</div>
                @for (f of featureImportance(); track f.name) {
                  <div class="fi-row">
                    <span class="fi-name">{{ f.name }}</span>
                    <div class="fi-bar-wrap">
                      <div class="fi-bar" [style.width.%]="f.pct"></div>
                    </div>
                    <span class="fi-val">{{ f.pct | number:'1.0-0' }}%</span>
                  </div>
                }
              </div>
            }

            <!-- ML vs legacy comparison note -->
            <div class="legacy-note">
              <div class="ln-title">ML vs Legacy regime</div>
              <div class="ln-body">
                The legacy system assigns regimes using a fixed daily-vol threshold
                (LOW &lt; 80 bps · MEDIUM 80–150 bps · HIGH &gt; 150 bps).
                The ML model clusters on <em>intraday</em> bar features — capturing
                regime changes within a single session that daily thresholds miss.
              </div>
            </div>
          </section>
        </div>
      }

      @if (detecting()) {
        <div class="loading-row">
          <div class="spinner"></div>
          <span class="muted">Running detection pipeline…</span>
        </div>
      }

      @if (error()) {
        <div class="error-box">
          <div class="err-header">
            <span class="err-status">HTTP {{ error()!.status }}</span>
            <span class="err-label">
              @if (error()!.status === 401) { Authentication error }
              @else if (error()!.status === 403) { Permission denied }
              @else if (error()!.status === 500) { Server error }
              @else { Request failed }
            </span>
          </div>
          <div class="err-detail">{{ error()!.message }}</div>
          @if (error()!.status === 401) {
            <div class="err-hint">
              The server restarted and your session token is no longer valid.
              Click <strong>Sign Out</strong> in the sidebar and log back in.
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    /* ── Layout ─────────────────────────────────────────────── */
    .page { padding: 2rem; background: #0f1923; min-height: 100vh; color: #d0dde8; }
    .page-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 0.5rem; }
    h2 { margin: 0 0 0.25rem; font-size: 1.5rem; display: flex; align-items: center; gap: 0.75rem; }
    .ml-badge { font-size: 0.68rem; padding: 0.2rem 0.55rem; border-radius: 10px;
        background: #1e3050; border: 1px solid #2a5080; color: #60a0d0; font-weight: 600;
        letter-spacing: 0.04em; text-transform: uppercase; }
    .page-intro { color: #7a8fa6; font-size: 0.88rem; max-width: 78ch; margin: 0.25rem 0 1.25rem; }

    /* ── Glossary ────────────────────────────────────────────── */
    details.dict-accordion { background: #131f2e; border: 1px solid #2a3f55; border-radius: 6px; margin-bottom: 1.5rem; }
    details.dict-accordion > summary { padding: 0.65rem 1rem; cursor: pointer; color: #a0b0c0;
        font-size: 0.82rem; font-weight: 600; list-style: none; display: flex;
        justify-content: space-between; align-items: center; user-select: none; }
    details.dict-accordion > summary::after { content: '▸'; font-size: 0.75rem; color: #7a8fa6; }
    details.dict-accordion[open] > summary::after { content: '▾'; }
    details.dict-accordion > summary::-webkit-details-marker { display: none; }
    .dict-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .dict-table th { text-align: left; padding: 0.5rem 1rem; color: #7a8fa6;
        border-bottom: 1px solid #2a3f55; font-size: 0.75rem; text-transform: uppercase; }
    .dict-table td { padding: 0.45rem 1rem; border-bottom: 1px solid #1f2f40; vertical-align: top; }
    .dict-table td:first-child { font-family: monospace; font-size: 0.78rem; color: #e0b44a;
        white-space: nowrap; width: 1%; padding-right: 1.5rem; }
    .dict-table tr:last-child td { border-bottom: none; }

    /* ── TCA Business Context accordion ─────────────────────── */
    .biz-accordion-body { padding: 0.75rem 1rem 1rem; }
    .biz-section-label { font-size: 0.72rem; color: #5a7080; text-transform: uppercase;
        letter-spacing: 0.06em; margin-bottom: 0.5rem; font-weight: 600; }
    .dict-table td.cost-low  { color: #40c080; font-weight: 600; }
    .dict-table td.cost-medium { color: #e0b44a; font-weight: 600; }
    .dict-table td.cost-high { color: #e07070; font-weight: 600; }
    .dict-table td.conf-high { font-family: monospace; font-size: 0.82rem; color: #40c080; white-space: nowrap; }
    .dict-table td.conf-low  { font-family: monospace; font-size: 0.82rem; color: #e0b44a; white-space: nowrap; }

    /* ── Status bar ─────────────────────────────────────────── */
    .status-bar { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem; }
    .status-chip { display: flex; align-items: center; gap: 0.5rem; flex: 1;
        padding: 0.45rem 0.85rem; border-radius: 6px; font-size: 0.8rem;
        background: #1a2533; border: 1px solid #2a3f55; color: #7a8fa6; }
    .status-chip.ready { border-color: #2a5040; color: #a0d0b0; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #3a3a3a; flex-shrink: 0; }
    .ready .dot { background: #40c080; box-shadow: 0 0 6px #40c08060; }
    .train-btn { padding: 0.45rem 1.1rem; background: #1e3050; border: 1px solid #2a5080;
        border-radius: 4px; color: #80c0e0; cursor: pointer; font-size: 0.82rem; white-space: nowrap; }
    .train-btn:hover:not(:disabled) { border-color: #e0b44a; color: #e0b44a; }
    .train-btn:disabled { opacity: 0.45; cursor: not-allowed; }
    .train-banner { background: #1a3a2a; border: 1px solid #2a6040; border-radius: 4px;
        padding: 0.5rem 1rem; font-size: 0.82rem; color: #40c080; margin-bottom: 1rem; }

    /* ── Controls ────────────────────────────────────────────── */
    .controls-row { display: flex; align-items: flex-end; gap: 1rem; margin-bottom: 1.5rem; }
    .ctrl-group { display: flex; flex-direction: column; gap: 0.3rem; }
    .ctrl-group label { color: #7a8fa6; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; }
    input[type="date"], select { padding: 0.5rem 0.75rem; background: #1a2533;
        border: 1px solid #2a3f55; border-radius: 4px; color: #d0dde8; font-size: 0.88rem; }
    input[type="date"]:focus, select:focus { outline: none; border-color: #e0b44a; }
    .detect-btn { padding: 0.55rem 1.75rem; background: #e0b44a; border: none; border-radius: 4px;
        color: #0f1923; font-weight: 700; cursor: pointer; font-size: 0.9rem; height: fit-content; }
    .detect-btn:hover:not(:disabled) { background: #c9a03e; }
    .detect-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    /* ── Not trained box ─────────────────────────────────────── */
    .not-trained-box { display: flex; align-items: flex-start; gap: 1rem;
        background: #1e2a3a; border: 1px solid #2a4a6a; border-radius: 6px;
        padding: 1.25rem; margin-bottom: 1.5rem; color: #80a0c0; font-size: 0.88rem; }
    .nt-icon { font-size: 1.8rem; line-height: 1; flex-shrink: 0; }

    /* ── Regime KPI cards ────────────────────────────────────── */
    .regime-kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-bottom: 1.75rem; }
    .regime-card { border-radius: 8px; padding: 1.25rem; border: 1px solid; }
    .regime-card-low    { background: #111e14; border-color: #1e4a2e; }
    .regime-card-medium { background: #1e1a10; border-color: #4a3a10; }
    .regime-card-high   { background: #1e1010; border-color: #4a1e1e; }
    .rc-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
    .rc-dot { width: 10px; height: 10px; border-radius: 50%; }
    .rc-dot-low    { background: #40c080; box-shadow: 0 0 8px #40c08080; }
    .rc-dot-medium { background: #e0b44a; box-shadow: 0 0 8px #e0b44a80; }
    .rc-dot-high   { background: #e07070; box-shadow: 0 0 8px #e0707080; }
    .rc-label { font-size: 0.85rem; font-weight: 700; letter-spacing: 0.05em;
        text-transform: uppercase; flex: 1; color: #d0dde8; }
    .rc-pct { font-size: 1.9rem; font-weight: 800; line-height: 1; }
    .regime-card-low    .rc-pct { color: #40c080; }
    .regime-card-medium .rc-pct { color: #e0b44a; }
    .regime-card-high   .rc-pct { color: #e07070; }
    .rc-bar-wrap { background: #0a0f14; border-radius: 3px; height: 6px; margin-bottom: 0.6rem; }
    .rc-bar { height: 6px; border-radius: 3px; transition: width 0.6s ease; }
    .rc-bar-low    { background: linear-gradient(90deg, #1a6040, #40c080); }
    .rc-bar-medium { background: linear-gradient(90deg, #6a4a10, #e0b44a); }
    .rc-bar-high   { background: linear-gradient(90deg, #6a1a1a, #e07070); }
    .rc-desc { font-size: 0.75rem; color: #7a8fa6; margin-bottom: 0.9rem; font-style: italic; }
    .rc-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem 0.75rem; margin-bottom: 0.6rem; }
    .rc-stat { display: flex; flex-direction: column; gap: 0.15rem; }
    .rcs-label { font-size: 0.68rem; color: #5a7080; text-transform: uppercase; letter-spacing: 0.05em; }
    .rcs-val { font-size: 0.85rem; font-weight: 600; color: #b0c8d8; }
    .rc-ticks { font-size: 0.72rem; color: #4a6070; border-top: 1px solid #1a2a3a;
        padding-top: 0.4rem; margin-top: 0.2rem; }
    .positive { color: #40c080 !important; }
    .negative { color: #e07070 !important; }

    /* ── Section headers ─────────────────────────────────────── */
    .section { background: #131f2e; border: 1px solid #2a3f55; border-radius: 8px;
        padding: 1.25rem; margin-bottom: 1.5rem; }
    .section-header { display: flex; align-items: baseline; gap: 0.75rem; margin-bottom: 1rem; }
    .section-title { font-size: 0.9rem; font-weight: 700; color: #c0d0e0;
        text-transform: uppercase; letter-spacing: 0.06em; }
    .section-sub { font-size: 0.76rem; color: #4a6070; }

    /* ── How-to-read callout ─────────────────────────────────── */
    .how-to-read { background: #0d1a28; border-left: 3px solid #2a4a6a;
        border-radius: 0 4px 4px 0; padding: 0.75rem 1rem; margin-bottom: 1rem;
        font-size: 0.8rem; color: #7a9ab0; line-height: 1.65; }
    .htr-label { display: inline-block; font-size: 0.65rem; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.08em; color: #4a7090;
        background: #1a3045; padding: 0.1rem 0.4rem; border-radius: 3px;
        margin-right: 0.5rem; vertical-align: middle; }
    .how-to-read strong { color: #a0c0d8; font-weight: 600; }
    .how-to-read em { color: #80a8c0; font-style: normal; }
    .htr-low   { color: #40c080 !important; }
    .htr-amber { color: #e0b44a !important; }
    .htr-red   { color: #e07070 !important; }

    /* ── Timeline ────────────────────────────────────────────── */
    .timeline-legend { display: flex; gap: 0.5rem; margin-bottom: 0.6rem; }
    .tl-chip { font-size: 0.7rem; padding: 0.15rem 0.5rem; border-radius: 3px;
        font-weight: 700; letter-spacing: 0.05em; }
    .tl-low    { background: #1a3a24; color: #40c080; border: 1px solid #2a6040; }
    .tl-medium { background: #3a2a10; color: #e0b44a; border: 1px solid #6a4a10; }
    .tl-high   { background: #3a1414; color: #e07070; border: 1px solid #6a2424; }
    .timeline-wrap { margin-bottom: 0.5rem; }
    .timeline-strip { display: flex; height: 44px; border-radius: 4px; overflow: hidden;
        border: 1px solid #1a2a3a; }
    .tl-bar { flex: 1; }
    .tl-bar.tl-low    { background: #1a4028; }
    .tl-bar.tl-medium { background: #3a2a10; }
    .tl-bar.tl-high   { background: #3a1414; }
    .tl-bar:hover { filter: brightness(1.4); }
    .timeline-axis { display: flex; justify-content: space-between;
        font-size: 0.7rem; color: #4a6070; padding: 0 2px; margin-top: 0.25rem; }
    .runs-summary { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.6rem; }
    .runs-label { font-size: 0.72rem; color: #5a7080; }
    .run-chip { font-size: 0.72rem; padding: 0.15rem 0.5rem; border-radius: 3px; font-weight: 600; }

    /* ── Analysis panels ─────────────────────────────────────── */
    .analysis-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }

    /* ── 3D Plotly container ─────────────────────────────────── */
    .scatter-section { }
    .plot-3d-container { width: 100%; height: 400px; background: #0a1420;
        border-radius: 6px; }
    .plot-3d-caption { font-size: 0.68rem; color: #4a6070; text-align: center; margin-top: 0.35rem; }

    /* ── Centroid table ──────────────────────────────────────── */
    .centroid-section { }
    .centroid-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-bottom: 1.25rem; }
    .centroid-table th { text-align: left; padding: 0.5rem 0.65rem; color: #5a7080;
        border-bottom: 1px solid #1f2f40; font-size: 0.72rem; text-transform: uppercase; }
    .centroid-table td { padding: 0.55rem 0.65rem; border-bottom: 1px solid #1a2a3a; }
    .row-low td:first-child    { border-left: 2px solid #40c080; }
    .row-medium td:first-child { border-left: 2px solid #e0b44a; }
    .row-high td:first-child   { border-left: 2px solid #e07070; }
    .regime-pill { display: inline-block; padding: 0.15rem 0.55rem; border-radius: 3px;
        font-size: 0.72rem; font-weight: 700; letter-spacing: 0.06em; }
    .pill-low    { background: #1a3a24; color: #40c080; border: 1px solid #2a6040; }
    .pill-medium { background: #3a2a10; color: #e0b44a; border: 1px solid #6a4a10; }
    .pill-high   { background: #3a1414; color: #e07070; border: 1px solid #6a2424; }

    /* ── Feature importance bars ─────────────────────────────── */
    .fi-section { margin-bottom: 1.25rem; }
    .fi-label { font-size: 0.72rem; color: #5a7080; text-transform: uppercase;
        letter-spacing: 0.05em; margin-bottom: 0.5rem; }
    .fi-row { display: grid; grid-template-columns: 100px 1fr 38px; gap: 0.4rem;
        align-items: center; margin-bottom: 0.35rem; font-size: 0.78rem; }
    .fi-name { color: #a0b0c0; }
    .fi-bar-wrap { background: #1a2533; border-radius: 2px; height: 5px; }
    .fi-bar { background: linear-gradient(90deg, #2a5080, #40a0d0); height: 5px; border-radius: 2px; }
    .fi-val { color: #5a7080; text-align: right; font-size: 0.72rem; }

    /* ── Legacy note ─────────────────────────────────────────── */
    .legacy-note { background: #0d1a28; border: 1px solid #1a2a3a; border-radius: 4px;
        padding: 0.85rem; }
    .ln-title { font-size: 0.72rem; color: #4a6070; text-transform: uppercase;
        letter-spacing: 0.05em; margin-bottom: 0.35rem; }
    .ln-body { font-size: 0.78rem; color: #6a8090; line-height: 1.55; }
    .ln-body em { color: #80a0b0; font-style: normal; }

    /* ── No data / plot placeholder ─────────────────────────── */
    .no-data-box { display: flex; align-items: center; justify-content: center;
        height: 400px; color: #4a6070; font-size: 0.85rem; background: #0a1420;
        border-radius: 6px; }
    .plot-loading { display: flex; flex-direction: column; align-items: center;
        justify-content: center; gap: 0.75rem; height: 400px; background: #0a1420;
        border-radius: 6px; }
    .plot-loading .spinner { width: 24px; height: 24px; }
    .plot-loading span { font-size: 0.8rem; color: #4a6070; }

    /* ── Loading & error ─────────────────────────────────────── */
    .loading-row { display: flex; align-items: center; gap: 0.75rem; padding: 1.5rem 0; color: #7a8fa6; }
    .spinner { width: 18px; height: 18px; border: 2px solid #2a3f55;
        border-top-color: #e0b44a; border-radius: 50%; animation: spin 0.7s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .muted { color: #7a8fa6; font-size: 0.88rem; }
    .error-box { background: #3a1a1a; border: 1px solid #6a2a2a; border-radius: 6px;
        padding: 1rem; color: #e07070; font-size: 0.85rem; margin-top: 1rem; }
    .err-header { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.5rem; }
    .err-status { font-family: monospace; font-size: 0.78rem; padding: 0.1rem 0.4rem;
        background: #5a1a1a; border-radius: 3px; color: #e07070; }
    .err-label { font-weight: 700; font-size: 0.82rem; color: #e08080; }
    .err-detail { font-family: monospace; font-size: 0.8rem; color: #c06060;
        word-break: break-all; white-space: pre-wrap; }
    .err-hint { margin-top: 0.6rem; font-size: 0.78rem; color: #a05050;
        border-top: 1px solid #5a2a2a; padding-top: 0.5rem; }
  `],
})
export class RegimeDetectionComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  tradeDate = '2025-01-15';
  selectedInstrument = 'EQTY-001';

  readonly equityInstruments = ['EQTY-001', 'EQTY-002', 'EQTY-003', 'EQTY-004', 'EQTY-005'];
  readonly futureInstruments = ['FUTS-001', 'FUTS-002', 'FUTS-003'];
  readonly bondInstruments   = ['BOND-001', 'BOND-002', 'BOND-003'];
  readonly fxInstruments     = ['FXFW-001', 'FXFW-002', 'FXFW-003'];

  status = signal<any>(null);
  modelReady = computed(() => !!this.status()?.ready);

  summary = signal<RegimeSummary[]>([]);
  scatterPoints = signal<ScatterPoint[]>([]);
  timelineData = signal<TimelineBar[]>([]);

  training = signal(false);
  detecting = signal(false);
  hasDetected = signal(false);
  loadingSummary = signal(false);
  trainResult = signal<any>(null);
  error = signal<{ status: number; message: string } | null>(null);

  readonly timeline = this.timelineData;

  readonly featureImportance = computed(() => {
    const s = this.summary();
    if (s.length < 2) return [];
    const vals = (fn: (r: RegimeSummary) => number) => s.map(fn);
    const spread = (arr: number[]) => Math.max(...arr) - Math.min(...arr);
    const raw = [
      { name: 'intraday_vol',  v: spread(vals(r => r.avg_intraday_vol)) },
      { name: 'volume_ratio',  v: spread(vals(r => r.avg_volume_ratio)) },
      { name: 'momentum',      v: spread(vals(r => r.avg_momentum)) },
    ];
    const total = raw.reduce((a, b) => a + b.v, 0) || 1;
    return raw.map(f => ({ name: f.name, pct: (f.v / total) * 100 }))
              .sort((a, b) => b.pct - a.pct);
  });

  readonly timelineRegimePcts = computed(() => {
    const tl = this.timelineData();
    if (!tl.length) return [];
    const total = tl.length;
    const counts: Record<string, number> = { LOW: 0, MEDIUM: 0, HIGH: 0 };
    tl.forEach(b => { counts[b.regime] = (counts[b.regime] ?? 0) + 1; });
    return Object.entries(counts)
      .map(([regime, n]) => ({ regime, pct: n / total * 100 }))
      .sort((a, b) => b.pct - a.pct);
  });

  constructor() {}

  isAdmin(): boolean {
    return this.auth.hasRole('ADMIN');
  }

  private parseError(err: any, fallback: string): { status: number; message: string } {
    const status: number = err?.status ?? 0;
    const detail: string = err?.error?.detail ?? err?.message ?? fallback;
    if (status === 401) {
      return { status, message: 'Session expired — sign out and log in again to get a fresh token.' };
    }
    if (status === 403) {
      return { status, message: detail || 'You do not have permission for this action (ADMIN role required).' };
    }
    return { status, message: detail || fallback };
  }

  ngOnInit(): void {
    this.loadStatus();
  }

  loadStatus(): void {
    this.api.getRegimeStatus().subscribe({
      next: s => {
        this.status.set(s);
        if (s?.ready) this.loadSummary();
      },
      error: () => {},
    });
  }

  onDateChange(): void {
    if (this.modelReady()) this.loadSummary();
  }

  loadSummary(): void {
    this.loadingSummary.set(true);
    this.api.getRegimeSummary(this.tradeDate).subscribe({
      next: data => { this.summary.set(data as RegimeSummary[]); this.loadingSummary.set(false); },
      error: () => { this.loadingSummary.set(false); },
    });
  }

  trainModel(): void {
    this.training.set(true);
    this.trainResult.set(null);
    this.error.set(null);
    this.api.trainRegime().subscribe({
      next: result => {
        this.trainResult.set(result);
        this.training.set(false);
        this.loadStatus();
      },
      error: err => {
        this.error.set(this.parseError(err, 'Training failed'));
        this.training.set(false);
      },
    });
  }

  runDetection(): void {
    this.detecting.set(true);
    this.error.set(null);
    this.scatterPoints.set([]);
    this.timelineData.set([]);

    forkJoin({
      scatter:  this.api.getRegimeDetect(this.tradeDate),
      timeline: this.api.getRegimeTimeline(this.tradeDate, this.selectedInstrument),
    }).subscribe({
      next: ({ scatter, timeline }) => {
        this.scatterPoints.set(scatter as ScatterPoint[]);
        this.timelineData.set(timeline as TimelineBar[]);
        this.detecting.set(false);
        this.hasDetected.set(true);
        setTimeout(() => this.renderPlot(), 0);
      },
      error: err => {
        this.error.set(this.parseError(err, 'Detection failed — model may not be trained yet.'));
        this.detecting.set(false);
        this.hasDetected.set(true);
      },
    });
  }

  private renderPlot(): void {
    const el = document.getElementById('regime-plot-3d');
    const Plotly = (window as any)['Plotly'];
    console.log('[RegimePlot] el=', el, 'Plotly=', typeof Plotly, 'pts=', this.scatterPoints().length);
    if (!el || !Plotly) return;
    this.renderPlot3D(this.scatterPoints(), el);
  }

  private renderPlot3D(pts: ScatterPoint[], el: HTMLElement): void {
    const Plotly = (window as any)['Plotly'];

    const palette: Record<string, string> = {
      LOW: '#40c080',
      MEDIUM: '#e0b44a',
      HIGH: '#e07070',
    };

    const traces = (['LOW', 'MEDIUM', 'HIGH'] as const).map(r => {
      const sub = pts.filter(p => p.regime === r);
      return {
        type: 'scatter3d',
        mode: 'markers',
        name: r,
        x: sub.map(p => +(p.intraday_vol * 10000).toFixed(2)),
        y: sub.map(p => +p.volume_ratio.toFixed(3)),
        z: sub.map(p => +(p.momentum * 10000).toFixed(2)),
        text: sub.map(p =>
          `<b>${p.instrument_id}</b><br>` +
          `Vol: ${(p.intraday_vol * 10000).toFixed(1)} bps<br>` +
          `VolZ: ${p.volume_ratio.toFixed(2)}σ<br>` +
          `Mom: ${(p.momentum * 10000).toFixed(1)} bps<br>` +
          `Conf: ${(p.cluster_confidence * 100).toFixed(0)}%`
        ),
        hovertemplate: '%{text}<extra>' + r + '</extra>',
        marker: {
          size: 3.5,
          color: palette[r],
          opacity: 0.8,
          line: { color: '#0d1824', width: 0.3 },
        },
      };
    });

    const axisBase = {
      color: '#4a6070',
      gridcolor: '#1a2a3a',
      zerolinecolor: '#253545',
      tickfont: { color: '#5a7080', size: 9 },
      showbackground: true,
      backgroundcolor: '#0d1824',
    };

    const layout = {
      paper_bgcolor: '#0a1420',
      scene: {
        bgcolor: '#0d1824',
        aspectmode: 'cube',
        camera: { eye: { x: 1.5, y: 1.5, z: 0.85 } },
        xaxis: { ...axisBase, title: { text: 'Price Range (bps)', font: { color: '#7a8fa6', size: 11 } } },
        yaxis: { ...axisBase, title: { text: 'Volume Z (σ)', font: { color: '#7a8fa6', size: 11 } } },
        zaxis: { ...axisBase, title: { text: 'Momentum (bps)', font: { color: '#7a8fa6', size: 11 } } },
      },
      legend: {
        x: 0.01, y: 0.99,
        font: { color: '#a0b0c0', size: 11 },
        bgcolor: '#131f2e',
        bordercolor: '#2a3f55',
        borderwidth: 1,
      },
      hoverlabel: {
        bgcolor: '#1a2533',
        bordercolor: '#2a3f55',
        font: { color: '#d0dde8', size: 11 },
      },
      margin: { l: 0, r: 0, b: 0, t: 0 },
      height: 400,
    };

    try {
      Plotly.newPlot(el, traces, layout, {
        responsive: true,
        displayModeBar: true,
        modeBarButtonsToRemove: ['toImage', 'sendDataToCloud'],
        displaylogo: false,
      });
      console.log('[RegimePlot] newPlot succeeded');
    } catch (e) {
      console.error('[RegimePlot] newPlot failed:', e);
    }
  }
}
