import { createFeatureSelector, createSelector } from '@ngrx/store';
import { AuthState } from './auth.reducer';

export const selectAuthState = createFeatureSelector<AuthState>('auth');

export const selectAuthLoading = createSelector(selectAuthState, s => s.loading);
export const selectAuthError = createSelector(selectAuthState, s => s.error);
export const selectCurrentUser = createSelector(selectAuthState, s => s.user);
export const selectIsAuthenticated = createSelector(
  selectAuthState,
  () => {
    const token = localStorage.getItem('tca_access_token');
    if (!token) return false;
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      return Date.now() / 1000 < payload.exp;
    } catch {
      return false;
    }
  },
);
export const selectUserRole = createSelector(selectCurrentUser, u => u?.role ?? null);
