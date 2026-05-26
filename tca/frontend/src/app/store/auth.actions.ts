import { createActionGroup, emptyProps, props } from '@ngrx/store';
import { TokenResponse } from '../core/services/api.service';

export const AuthActions = createActionGroup({
  source: 'Auth',
  events: {
    Login: props<{ clientId: string; clientSecret: string }>(),
    'Login Success': props<{ response: TokenResponse }>(),
    'Login Failure': props<{ error: string }>(),
    Logout: emptyProps(),
    'Refresh Token': emptyProps(),
    'Refresh Token Success': props<{ response: TokenResponse }>(),
    'Refresh Token Failure': emptyProps(),
  },
});
