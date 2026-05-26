import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { Actions, createEffect, ofType } from '@ngrx/effects';
import { catchError, map, of, switchMap, tap } from 'rxjs';
import { ApiService } from '../core/services/api.service';
import { AuthService } from '../core/auth/auth.service';
import { AuthActions } from './auth.actions';

@Injectable()
export class AuthEffects {
  private readonly actions$ = inject(Actions);
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  login$ = createEffect(() =>
    this.actions$.pipe(
      ofType(AuthActions.login),
      switchMap(({ clientId, clientSecret }) =>
        this.api.login(clientId, clientSecret).pipe(
          map(response => AuthActions.loginSuccess({ response })),
          catchError(err => {
            let message: string;
            if (err?.error?.detail) {
              message = typeof err.error.detail === 'string'
                ? err.error.detail
                : JSON.stringify(err.error.detail);
            } else if (err?.status === 0) {
              message = 'Cannot reach server — check that the API container is running.';
            } else if (err?.status) {
              message = `Login failed (HTTP ${err.status})`;
            } else if (err?.message) {
              message = err.message;
            } else {
              message = 'Login failed';
            }
            return of(AuthActions.loginFailure({ error: message }));
          }),
        ),
      ),
    ),
  );

  loginSuccess$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(AuthActions.loginSuccess),
        tap(({ response }) => {
          this.auth.storeTokens(response.access_token, response.refresh_token);
          const user = this.auth.currentUser();
          const role = user?.role;
          if (role === 'CLIENT') {
            this.router.navigate(['/client-view']);
          } else {
            this.router.navigate(['/dashboard']);
          }
        }),
      ),
    { dispatch: false },
  );

  logout$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(AuthActions.logout),
        tap(() => {
          this.auth.logout();
          this.router.navigate(['/login']);
        }),
      ),
    { dispatch: false },
  );
}
