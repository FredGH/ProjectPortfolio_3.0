import { Routes } from '@angular/router';
import { authGuard } from './core/auth/auth.guard';
import { roleGuard } from './core/auth/role.guard';

export const routes: Routes = [
  { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
  {
    path: 'login',
    loadComponent: () =>
      import('./features/login/login.component').then(m => m.LoginComponent),
  },
  {
    path: 'dashboard',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/dashboard/dashboard.component').then(m => m.DashboardComponent),
  },
  {
    path: 'order-tca',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/order-tca/order-tca.component').then(m => m.OrderTcaComponent),
  },
  {
    path: 'submit-fill',
    canActivate: [authGuard, roleGuard(['TRADER', 'HEAD_OF_TRADING', 'ADMIN'])],
    loadComponent: () =>
      import('./features/submit-fill/submit-fill.component').then(m => m.SubmitFillComponent),
  },
  {
    path: 'algo-perf',
    canActivate: [authGuard, roleGuard(['TRADER', 'HEAD_OF_TRADING', 'COMPLIANCE', 'ADMIN'])],
    loadComponent: () =>
      import('./features/algo-perf/algo-perf.component').then(m => m.AlgoPerfComponent),
  },
  {
    path: 'alpha-decay',
    canActivate: [authGuard, roleGuard(['TRADER', 'HEAD_OF_TRADING', 'COMPLIANCE', 'ADMIN'])],
    loadComponent: () =>
      import('./features/alpha-decay/alpha-decay.component').then(m => m.AlphaDecayComponent),
  },
  {
    path: 'venue-sor',
    canActivate: [authGuard, roleGuard(['TRADER', 'HEAD_OF_TRADING', 'COMPLIANCE', 'ADMIN'])],
    loadComponent: () =>
      import('./features/venue-sor/venue-sor.component').then(m => m.VenueSorComponent),
  },
  {
    path: 'mifid',
    canActivate: [authGuard, roleGuard(['COMPLIANCE', 'ADMIN'])],
    loadComponent: () =>
      import('./features/mifid/mifid.component').then(m => m.MifidComponent),
  },
  {
    path: 'client-view',
    canActivate: [authGuard, roleGuard(['CLIENT'])],
    loadComponent: () =>
      import('./features/client-view/client-view.component').then(m => m.ClientViewComponent),
  },
  {
    path: 'pre-trade',
    canActivate: [authGuard, roleGuard(['TRADER', 'HEAD_OF_TRADING', 'COMPLIANCE', 'ADMIN'])],
    loadComponent: () =>
      import('./features/pre-trade/pre-trade.component').then(m => m.PreTradeComponent),
  },
  {
    path: 'regime-detection',
    canActivate: [authGuard, roleGuard(['TRADER', 'HEAD_OF_TRADING', 'COMPLIANCE', 'ADMIN'])],
    loadComponent: () =>
      import('./features/regime-detection/regime-detection.component').then(m => m.RegimeDetectionComponent),
  },
  {
    path: 'glossary',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/glossary/glossary.component').then(m => m.GlossaryComponent),
  },
  { path: '**', redirectTo: 'dashboard' },
];
