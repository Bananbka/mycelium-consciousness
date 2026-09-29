export type Role = 'admin' | 'clone';
export type Tier = 'free' | 'standard' | 'premium';

export interface User { id: number; email: string; role: Role; is_active: boolean; created_at: string; }
export interface Profile { id: number; designation: string; status: string; subscription_tier: Tier; user_id: number | null; created_at: string; }
export interface Backup { id: number; clone_id: number; period_start: string; period_end: string; entry_count: number; created_at: string; restored_at: string | null; }
export interface BackupDetail extends Backup { payload: Record<string, unknown>[]; }
export interface Stats { users: number; clones: number; backups: number; }
export interface Rollup { clone_id: number; status: string; entry_count: number; }
export interface Home { message: string; role: Role; email: string; }
