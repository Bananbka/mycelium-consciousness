import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api } from './api';
import { Role, User } from './models';

const TOKEN_KEY = 'mycelium.token';

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const token = localStorage.getItem(TOKEN_KEY);
  return next(token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req);
};

export function errText(e: unknown): string {
  if (e instanceof HttpErrorResponse) {
    const d = e.error?.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x) => x.msg).join('; ');
    return `${e.status} ${e.statusText}`;
  }
  return 'Unexpected error';
}

@Injectable({ providedIn: 'root' })
export class Auth {
  private api = inject(Api);
  readonly user = signal<User | null>(null);

  get token(): string | null { return localStorage.getItem(TOKEN_KEY); }

  async login(email: string, password: string): Promise<User> {
    const t = await firstValueFrom(this.api.login(email, password));
    localStorage.setItem(TOKEN_KEY, t.access_token);
    return this.load();
  }

  async load(): Promise<User> {
    const u = await firstValueFrom(this.api.me());
    this.user.set(u);
    return u;
  }

  logout(): void {
    localStorage.removeItem(TOKEN_KEY);
    this.user.set(null);
  }
}

export const homeFor = (role: Role) => (role === 'admin' ? '/admin' : '/clone');

export const roleGuard = (role: Role): CanActivateFn => async () => {
  const auth = inject(Auth);
  const router = inject(Router);
  if (!auth.token) return router.parseUrl('/login');
  try {
    const u = auth.user() ?? (await auth.load());
    return u.role === role ? true : router.parseUrl(homeFor(u.role));
  } catch {
    auth.logout();
    return router.parseUrl('/login');
  }
};
