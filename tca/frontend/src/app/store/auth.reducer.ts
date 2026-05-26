import { createReducer, on } from '@ngrx/store';
import { AuthActions } from './auth.actions';
import { UserClaims } from '../core/auth/auth.service';

export interface AuthState {
  user: UserClaims | null;
  loading: boolean;
  error: string | null;
}

const initialState: AuthState = {
  user: null,
  loading: false,
  error: null,
};

export const authReducer = createReducer(
  initialState,
  on(AuthActions.login, state => ({ ...state, loading: true, error: null })),
  on(AuthActions.loginSuccess, (state, { response: _ }) => ({
    ...state,
    loading: false,
    error: null,
  })),
  on(AuthActions.loginFailure, (state, { error }) => ({
    ...state,
    loading: false,
    error,
  })),
  on(AuthActions.logout, () => ({ ...initialState })),
  on(AuthActions.refreshTokenSuccess, state => ({ ...state, error: null })),
  on(AuthActions.refreshTokenFailure, () => ({ ...initialState })),
);
