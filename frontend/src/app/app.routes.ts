import { Routes } from '@angular/router';

import { roleGuard } from './auth';
import { AdminPage } from './pages/admin';
import { ClonePage } from './pages/clone';
import { LoginPage } from './pages/login';

export const routes: Routes = [
  { path: 'login', component: LoginPage },
  { path: 'clone', component: ClonePage, canActivate: [roleGuard('clone')] },
  { path: 'admin', component: AdminPage, canActivate: [roleGuard('admin')] },
  { path: '**', redirectTo: 'login' },
];
