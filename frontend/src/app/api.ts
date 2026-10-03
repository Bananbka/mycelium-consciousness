import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';

import { Backup, BackupDetail, Home, Profile, Rollup, Stats, Tier, User } from './models';

const B = '/api';

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  // auth
  login = (email: string, password: string) => this.http.post<{ access_token: string }>(`${B}/auth/login`, { email, password });
  register = (email: string, password: string, designation: string) => this.http.post<User>(`${B}/auth/register`, { email, password, designation });
  me = () => this.http.get<User>(`${B}/auth/me`);

  // clone
  cloneHome = () => this.http.get<Home>(`${B}/me/home`);
  profile = () => this.http.get<Profile>(`${B}/profiles/me`);
  updateProfile = (body: Partial<Pick<Profile, 'designation' | 'status'>>) => this.http.patch<Profile>(`${B}/profiles/me`, body);
  write = (content: string) => this.http.post<{ status: string }>(`${B}/memories/write`, { content });
  streamStatus = () => this.http.get<{ buffered_entries: number }>(`${B}/memories/stream/status`);
  backups = () => this.http.get<Backup[]>(`${B}/memories/backups`);
  backup = (id: number) => this.http.get<BackupDetail>(`${B}/memories/backups/${id}`);
  restore = (id: number) => this.http.post<BackupDetail>(`${B}/memories/backups/${id}/restore`, {});
  resurrect = (source_profile_id: number) => this.http.post<BackupDetail>(`${B}/memories/resurrect`, { source_profile_id });

  // admin
  adminHome = () => this.http.get<Home>(`${B}/admin/home`);
  stats = () => this.http.get<Stats>(`${B}/admin/stats`);
  users = () => this.http.get<User[]>(`${B}/admin/users`);
  deactivate = (id: number) => this.http.post<User>(`${B}/admin/users/${id}/deactivate`, {});
  clones = () => this.http.get<Profile[]>(`${B}/admin/clones`);
  setTier = (id: number, subscription_tier: Tier) => this.http.patch<Profile>(`${B}/admin/clones/${id}/subscription`, { subscription_tier });
  cloneBackups = (id: number) => this.http.get<Backup[]>(`${B}/admin/clones/${id}/backups`);
  rollup = (id: number) => this.http.post<Rollup>(`${B}/admin/clones/${id}/rollup`, {});
  setStatus = (id: number, status: string) => this.http.patch<Profile>(`${B}/profiles/${id}`, { status });
}
