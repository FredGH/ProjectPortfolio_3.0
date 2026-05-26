import { Injectable, signal } from '@angular/core';
import { jwtDecode } from 'jwt-decode';

export interface UserClaims {
  sub: string;
  role: string;
  counterparty_id: string | null;
  legal_entity: string | null;
  exp: number;
}

const ACCESS_KEY = 'tca_access_token';
const REFRESH_KEY = 'tca_refresh_token';

@Injectable({ providedIn: 'root' })
export class AuthService {
  readonly currentUser = signal<UserClaims | null>(this._loadUser());

  storeTokens(accessToken: string, refreshToken: string): void {
    localStorage.setItem(ACCESS_KEY, accessToken);
    localStorage.setItem(REFRESH_KEY, refreshToken);
    this.currentUser.set(this._decode(accessToken));
  }

  getAccessToken(): string | null {
    return localStorage.getItem(ACCESS_KEY);
  }

  isAuthenticated(): boolean {
    const user = this.currentUser();
    if (!user) return false;
    return Date.now() / 1000 < user.exp;
  }

  hasRole(...roles: string[]): boolean {
    const user = this.currentUser();
    return !!user && roles.includes(user.role);
  }

  logout(): void {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    this.currentUser.set(null);
  }

  private _loadUser(): UserClaims | null {
    const token = localStorage.getItem(ACCESS_KEY);
    return token ? this._decode(token) : null;
  }

  private _decode(token: string): UserClaims | null {
    try {
      return jwtDecode<UserClaims>(token);
    } catch {
      return null;
    }
  }
}
