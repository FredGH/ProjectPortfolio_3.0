import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from './auth.service';

export const roleGuard =
  (allowedRoles: string[]): CanActivateFn =>
  (): boolean | import('@angular/router').UrlTree => {
    const auth = inject(AuthService);
    const router = inject(Router);
    return auth.hasRole(...allowedRoles)
      ? true
      : router.createUrlTree(['/dashboard']);
  };
