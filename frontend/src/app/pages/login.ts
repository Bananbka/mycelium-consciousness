import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api } from '../api';
import { Auth, errText, homeFor } from '../auth';

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  template: `
    <div class="center">
      <form class="card auth" (ngSubmit)="submit()">
        <div class="orbit"></div>
        <h1>Mycelium</h1>
        <div class="sub">{{ mode() === 'login' ? 'Neural link authentication' : 'Clone registration' }}</div>

        <label>Email</label>
        <input name="email" type="email" [(ngModel)]="email" required autocomplete="username" />
        @if (mode() === 'register') {
          <label>Designation</label>
          <input name="designation" [(ngModel)]="designation" minlength="3" required placeholder="Clone-Alpha-7" />
        }
        <label>Password</label>
        <input name="password" type="password" [(ngModel)]="password" required autocomplete="current-password" />

        <button type="submit" [disabled]="busy()">{{ mode() === 'login' ? 'Connect' : 'Create clone' }}</button>
        @if (error()) {
          <p class="msg err">{{ error() }}</p>
        }
        <p class="muted" style="text-align: center">
          @if (mode() === 'login') {
            New clone? <a (click)="switchTo('register')">Register</a>
          } @else {
            Already linked? <a (click)="switchTo('login')">Sign in</a>
          }
        </p>
      </form>
    </div>
  `,
})
export class LoginPage {
  private api = inject(Api);
  private auth = inject(Auth);
  private router = inject(Router);

  mode = signal<'login' | 'register'>('login');
  busy = signal(false);
  error = signal('');
  email = '';
  password = '';
  designation = '';

  switchTo(m: 'login' | 'register') {
    this.mode.set(m);
    this.error.set('');
  }

  async submit() {
    this.busy.set(true);
    this.error.set('');
    try {
      if (this.mode() === 'register') {
        await firstValueFrom(this.api.register(this.email, this.password, this.designation));
      }
      const u = await this.auth.login(this.email, this.password);
      await this.router.navigateByUrl(homeFor(u.role));
    } catch (e) {
      this.error.set(errText(e));
    } finally {
      this.busy.set(false);
    }
  }
}
