import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api } from '../api';
import { Auth, errText } from '../auth';
import { Backup, Profile, Stats, Tier, User } from '../models';

const STATUSES = ['active', 'deceased', 'offline'];

@Component({
  selector: 'app-admin',
  imports: [DatePipe],
  template: `
    <div class="shell">
      <div class="top">
        <div class="brand">Mycelium <b>//</b> Command Deck</div>
        <div class="who">
          <span>{{ auth.user()?.email }}</span>
          <button class="ghost sm" (click)="logout()">Disconnect</button>
        </div>
      </div>
      <p class="muted">{{ greeting() }}</p>

      <div class="stats">
        <div class="card tile"><div class="n">{{ stats()?.users ?? '–' }}</div><div class="l">Users</div></div>
        <div class="card tile"><div class="n">{{ stats()?.clones ?? '–' }}</div><div class="l">Clones</div></div>
        <div class="card tile"><div class="n">{{ stats()?.backups ?? '–' }}</div><div class="l">Backups</div></div>
      </div>
      <p class="msg" [class.ok]="!isErr()" [class.err]="isErr()">{{ msg() }}</p>

      <div class="grid">
        <section class="card wide">
          <h2>Clone registry</h2>
          <table>
            <thead><tr><th>ID</th><th>Designation</th><th>Status</th><th>Tier</th><th></th></tr></thead>
            <tbody>
              @for (c of clones(); track c.id) {
                <tr>
                  <td>{{ c.id }}</td>
                  <td>{{ c.designation }}</td>
                  <td>
                    <select (change)="setStatus(c, $any($event.target).value)">
                      @for (s of statusOptions(c); track s) {
                        <option [selected]="s === c.status">{{ s }}</option>
                      }
                    </select>
                  </td>
                  <td>
                    <select (change)="setTier(c, $any($event.target).value)">
                      @for (t of tiers; track t) {
                        <option [selected]="t === c.subscription_tier">{{ t }}</option>
                      }
                    </select>
                  </td>
                  <td class="actions">
                    <button class="ghost sm" (click)="showBackups(c)">Backups</button>
                    <button class="sm" (click)="rollup(c)">Force rollup</button>
                  </td>
                </tr>
              } @empty {
                <tr><td colspan="5" class="muted">No clones registered.</td></tr>
              }
            </tbody>
          </table>
          @if (selected(); as s) {
            <h2 style="margin-top: 1rem">Backups of {{ s.designation }}</h2>
            <table>
              <thead><tr><th>#</th><th>Period</th><th>Frames</th><th>Restored</th></tr></thead>
              <tbody>
                @for (b of backups(); track b.id) {
                  <tr>
                    <td>{{ b.id }}</td>
                    <td>{{ b.period_start | date: 'short' }} → {{ b.period_end | date: 'short' }}</td>
                    <td>{{ b.entry_count }}</td>
                    <td>{{ b.restored_at ? (b.restored_at | date: 'short') : '—' }}</td>
                  </tr>
                } @empty {
                  <tr><td colspan="4" class="muted">No backups.</td></tr>
                }
              </tbody>
            </table>
          }
        </section>

        <section class="card wide">
          <h2>Users</h2>
          <table>
            <thead><tr><th>ID</th><th>Email</th><th>Role</th><th>State</th><th></th></tr></thead>
            <tbody>
              @for (u of users(); track u.id) {
                <tr>
                  <td>{{ u.id }}</td>
                  <td>{{ u.email }}</td>
                  <td><span class="badge">{{ u.role }}</span></td>
                  <td>
                    <span class="badge" [class.ok]="u.is_active" [class.bad]="!u.is_active">
                      {{ u.is_active ? 'active' : 'deactivated' }}
                    </span>
                  </td>
                  <td>
                    <button class="danger sm" [disabled]="!u.is_active || u.id === auth.user()?.id" (click)="deactivate(u)">
                      Deactivate
                    </button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </section>
      </div>
    </div>
  `,
})
export class AdminPage implements OnInit {
  private api = inject(Api);
  private router = inject(Router);
  auth = inject(Auth);

  tiers: Tier[] = ['free', 'standard', 'premium'];
  greeting = signal('');
  stats = signal<Stats | null>(null);
  users = signal<User[]>([]);
  clones = signal<Profile[]>([]);
  selected = signal<Profile | null>(null);
  backups = signal<Backup[]>([]);
  msg = signal('');
  isErr = signal(false);

  ngOnInit() {
    this.api.adminHome().subscribe((h) => this.greeting.set(h.message));
    this.refresh();
  }

  statusOptions(c: Profile): string[] {
    return STATUSES.includes(c.status) ? STATUSES : [c.status, ...STATUSES];
  }

  private async refresh() {
    const [stats, users, clones] = await Promise.all([
      firstValueFrom(this.api.stats()),
      firstValueFrom(this.api.users()),
      firstValueFrom(this.api.clones()),
    ]);
    this.stats.set(stats);
    this.users.set(users);
    this.clones.set(clones);
  }

  private async run(action: () => Promise<string>) {
    try {
      this.msg.set(await action());
      this.isErr.set(false);
    } catch (e) {
      this.msg.set(errText(e));
      this.isErr.set(true);
    }
    await this.refresh();
  }

  setTier(c: Profile, tier: Tier) {
    return this.run(async () => {
      await firstValueFrom(this.api.setTier(c.id, tier));
      return `${c.designation} → ${tier}`;
    });
  }

  setStatus(c: Profile, status: string) {
    return this.run(async () => {
      await firstValueFrom(this.api.setStatus(c.id, status));
      return `${c.designation} status → ${status}`;
    });
  }

  rollup(c: Profile) {
    return this.run(async () => {
      const r = await firstValueFrom(this.api.rollup(c.id));
      if (this.selected()?.id === c.id) await this.showBackups(c);
      return `Rollup of ${c.designation}: ${r.status} (${r.entry_count} frames)`;
    });
  }

  deactivate(u: User) {
    return this.run(async () => {
      await firstValueFrom(this.api.deactivate(u.id));
      return `${u.email} deactivated`;
    });
  }

  async showBackups(c: Profile) {
    this.selected.set(c);
    this.backups.set(await firstValueFrom(this.api.cloneBackups(c.id)));
  }

  logout() {
    this.auth.logout();
    this.router.navigateByUrl('/login');
  }
}
