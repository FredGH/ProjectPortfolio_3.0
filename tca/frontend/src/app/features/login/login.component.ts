import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Store } from '@ngrx/store';
import { AuthActions } from '../../store/auth.actions';
import { selectAuthLoading, selectAuthError } from '../../store/auth.selectors';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="login-container">
      <div class="login-card">
        <h1>PrivateBank TCA</h1>
        <p class="subtitle">Transaction Cost Analysis Platform</p>

        <form (ngSubmit)="onSubmit()" #f="ngForm">
          <div class="field">
            <label for="clientId">Client ID</label>
            <input
              id="clientId"
              type="text"
              [(ngModel)]="clientId"
              name="clientId"
              required
              autocomplete="username"
              placeholder="e.g. pb_admin"
            />
          </div>

          <div class="field">
            <label for="clientSecret">Client Secret</label>
            <input
              id="clientSecret"
              type="password"
              [(ngModel)]="clientSecret"
              name="clientSecret"
              required
              autocomplete="current-password"
              placeholder="••••••••"
            />
          </div>

          @if (error$ | async; as err) {
            <div class="error-banner">{{ err }}</div>
          }

          <button type="submit" [disabled]="loading$ | async">
            @if (loading$ | async) {
              Signing in…
            } @else {
              Sign In
            }
          </button>
        </form>
      </div>
    </div>
  `,
  styles: [`
    .login-container {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      background: #0f1923;
    }
    .login-card {
      background: #1a2533;
      border: 1px solid #2a3f55;
      border-radius: 8px;
      padding: 2.5rem;
      width: 100%;
      max-width: 400px;
    }
    h1 { color: #e0b44a; margin: 0 0 0.25rem; font-size: 1.75rem; }
    .subtitle { color: #7a8fa6; margin: 0 0 2rem; font-size: 0.9rem; }
    .field { margin-bottom: 1.25rem; }
    label { display: block; color: #a0b0c0; font-size: 0.85rem; margin-bottom: 0.4rem; }
    input {
      width: 100%;
      box-sizing: border-box;
      padding: 0.65rem 0.85rem;
      background: #0f1923;
      border: 1px solid #2a3f55;
      border-radius: 4px;
      color: #d0dde8;
      font-size: 0.95rem;
    }
    input:focus { outline: none; border-color: #e0b44a; }
    .error-banner {
      background: #3a1a1a;
      border: 1px solid #7a2020;
      border-radius: 4px;
      color: #e07070;
      padding: 0.6rem 0.85rem;
      font-size: 0.85rem;
      margin-bottom: 1rem;
    }
    button {
      width: 100%;
      padding: 0.75rem;
      background: #e0b44a;
      border: none;
      border-radius: 4px;
      color: #0f1923;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
    }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    button:hover:not(:disabled) { background: #c9a03e; }
  `],
})
export class LoginComponent {
  private readonly store = inject(Store);

  loading$ = this.store.select(selectAuthLoading);
  error$ = this.store.select(selectAuthError);

  clientId = '';
  clientSecret = '';

  onSubmit(): void {
    if (!this.clientId || !this.clientSecret) return;
    this.store.dispatch(
      AuthActions.login({ clientId: this.clientId, clientSecret: this.clientSecret }),
    );
  }
}
