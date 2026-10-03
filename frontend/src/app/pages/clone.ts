import { DatePipe, JsonPipe } from '@angular/common';
import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api } from '../api';
import { Auth, errText } from '../auth';
import { Backup, BackupDetail, Profile } from '../models';

@Component({
  selector: 'app-clone',
  imports: [FormsModule, DatePipe, JsonPipe],
  template: `
    <div class="shell">
      <div class="top">
        <div class="brand">Mycelium <b>//</b> Clone Console</div>
        <div class="who">
          <span>{{ auth.user()?.email }}</span>
          <button class="ghost sm" (click)="logout()">Disconnect</button>
        </div>
      </div>
      <p class="muted">{{ greeting() }}</p>

      <div class="grid">
        <section class="card">
          <h2>Identity</h2>
          @if (profile(); as p) {
            <p>
              ID <b>#{{ p.id }}</b> · <span class="badge">{{ p.subscription_tier }}</span>
              <span class="badge" [class.ok]="p.status === 'active'" [class.bad]="p.status !== 'active'">{{ p.status }}</span>
            </p>
            <label>Designation</label>
            <input [(ngModel)]="designation" />
            <label>Status</label>
            <input [(ngModel)]="status" />
            <button (click)="saveProfile()">Update profile</button>
            <p class="msg" [class.ok]="!profileErr()" [class.err]="profileErr()">{{ profileMsg() }}</p>
          }
        </section>

        <section class="card">
          <h2>Memory uplink</h2>
          <p class="muted">Buffered in stream: <b style="color: var(--accent)">{{ buffered() }}</b> frames</p>
          <label>Memory frame</label>
          <textarea [(ngModel)]="content" placeholder="What did you see?"></textarea>
          <button (click)="write()" [disabled]="!content.trim()">Transmit</button>
          <p class="msg" [class.ok]="!writeErr()" [class.err]="writeErr()">{{ writeMsg() }}</p>
        </section>

        <section class="card">
          <h2>Resurrection protocol</h2>
          <p class="muted">Seed your memory from a deceased clone's latest backup.</p>
          <label>Source clone ID</label>
          <input type="number" [(ngModel)]="sourceId" />
          <button (click)="resurrect()" [disabled]="!sourceId">Resurrect</button>
          <p class="msg" [class.ok]="!resErr()" [class.err]="resErr()">{{ resMsg() }}</p>
        </section>

        <section class="card wide">
          <h2>Backup vault</h2>
          <table>
            <thead><tr><th>#</th><th>Period</th><th>Frames</th><th>Restored</th><th></th></tr></thead>
            <tbody>
              @for (b of backups(); track b.id) {
                <tr>
                  <td>{{ b.id }}</td>
                  <td>{{ b.period_start | date: 'short' }} → {{ b.period_end | date: 'short' }}</td>
                  <td>{{ b.entry_count }}</td>
                  <td>{{ b.restored_at ? (b.restored_at | date: 'short') : '—' }}</td>
                  <td class="actions">
                    <button class="ghost sm" (click)="inspect(b)">Inspect</button>
                    <button class="sm" (click)="restore(b)">Restore</button>
                  </td>
                </tr>
              } @empty {
                <tr><td colspan="5" class="muted">No backups yet. Transmit memories and wait for a rollup.</td></tr>
              }
            </tbody>
          </table>
          @if (detail(); as d) {
            <h2 style="margin-top: 1rem">Backup #{{ d.id }} payload ({{ d.payload.length }})</h2>
            <pre>{{ d.payload | json }}</pre>
          }
          <p class="msg err">{{ vaultErr() }}</p>
        </section>
      </div>
    </div>
  `,
})
export class ClonePage implements OnInit, OnDestroy {
  private api = inject(Api);
  private router = inject(Router);
  auth = inject(Auth);

  greeting = signal('');
  profile = signal<Profile | null>(null);
  buffered = signal(0);
  backups = signal<Backup[]>([]);
  detail = signal<BackupDetail | null>(null);
  profileMsg = signal('');
  profileErr = signal(false);
  writeMsg = signal('');
  writeErr = signal(false);
  resMsg = signal('');
  resErr = signal(false);
  vaultErr = signal('');

  designation = '';
  status = '';
  content = '';
  sourceId: number | null = null;
  private timer?: ReturnType<typeof setInterval>;

  async ngOnInit() {
    this.api.cloneHome().subscribe((h) => this.greeting.set(h.message));
    await this.loadProfile();
    await this.loadBackups();
    await this.pollBuffer();
    this.timer = setInterval(() => this.pollBuffer(), 4000);
  }

  ngOnDestroy() {
    clearInterval(this.timer);
  }

  private async loadProfile() {
    const p = await firstValueFrom(this.api.profile());
    this.profile.set(p);
    this.designation = p.designation;
    this.status = p.status;
  }

  private async loadBackups() {
    this.backups.set(await firstValueFrom(this.api.backups()));
  }

  private async pollBuffer() {
    try {
      this.buffered.set((await firstValueFrom(this.api.streamStatus())).buffered_entries);
    } catch {
      /* transient; next tick retries */
    }
  }

  async saveProfile() {
    try {
      const p = await firstValueFrom(this.api.updateProfile({ designation: this.designation, status: this.status }));
      this.profile.set(p);
      this.profileErr.set(false);
      this.profileMsg.set('Profile synchronised.');
    } catch (e) {
      this.profileErr.set(true);
      this.profileMsg.set(errText(e));
    }
  }

  async write() {
    try {
      await firstValueFrom(this.api.write(this.content));
      this.content = '';
      this.writeErr.set(false);
      this.writeMsg.set('Frame recorded.');
      await this.pollBuffer();
    } catch (e) {
      this.writeErr.set(true);
      this.writeMsg.set(errText(e));
    }
  }

  async resurrect() {
    try {
      const b = await firstValueFrom(this.api.resurrect(this.sourceId!));
      this.resErr.set(false);
      this.resMsg.set(`Memory imported as backup #${b.id} (${b.entry_count} frames).`);
      await this.loadBackups();
    } catch (e) {
      this.resErr.set(true);
      this.resMsg.set(errText(e));
    }
  }

  async inspect(b: Backup) {
    try {
      this.vaultErr.set('');
      this.detail.set(await firstValueFrom(this.api.backup(b.id)));
    } catch (e) {
      this.vaultErr.set(errText(e));
    }
  }

  async restore(b: Backup) {
    try {
      this.vaultErr.set('');
      this.detail.set(await firstValueFrom(this.api.restore(b.id)));
      await this.loadBackups();
    } catch (e) {
      this.vaultErr.set(errText(e));
    }
  }

  logout() {
    this.auth.logout();
    this.router.navigateByUrl('/login');
  }
}
