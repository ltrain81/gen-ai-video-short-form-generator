import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Multiselect from '@cloudscape-design/components/multiselect';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import Tabs from '@cloudscape-design/components/tabs';
import { useCallback, useEffect, useState } from 'react';
import { useApp } from '../App';
import { api } from '../api/client';
import type { Group, TenantUser } from '../api/types';
import { useI18n } from '../i18n';
import { fmtDate } from '../util';

export default function AdminPage() {
  const { t } = useI18n();
  const { notify } = useApp();
  const [users, setUsers] = useState<TenantUser[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [members, setMembers] = useState<Record<string, string[]>>({});
  const [audit, setAudit] = useState<Record<string, unknown>[]>([]);
  const [email, setEmail] = useState('');
  const [groupName, setGroupName] = useState('');

  const load = useCallback(async () => {
    try {
      const [u, g, a] = await Promise.all([api.users(), api.groups(), api.audit()]);
      setUsers(u); setGroups(g); setAudit(a);
      setMembers(Object.fromEntries(await Promise.all(g.map(async (x) => [x.groupId, await api.groupMembers(x.groupId)] as const))));
    } catch (e) { notify('error', (e as Error).message); }
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  const emailOf = (sub: string) => users.find((u) => u.sub === sub)?.email ?? sub;
  const setGroupMembers = async (gid: string, next: string[]) => {
    const cur = members[gid] ?? [];
    await Promise.all([
      ...next.filter((s) => !cur.includes(s)).map((s) => api.addGroupMember(gid, s)),
      ...cur.filter((s) => !next.includes(s)).map((s) => api.removeGroupMember(gid, s)),
    ]).catch((e) => notify('error', e.message));
    load();
  };

  return (
    <ContentLayout header={<Header variant="h1">{t('navAdmin')}</Header>}>
      <Tabs tabs={[
        {
          id: 'users', label: t('users'), content: (
            <Table items={users} header={<Header actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Input value={email} placeholder={t('email')} onChange={(e) => setEmail(e.detail.value)} />
                <Button variant="primary" onClick={async () => { try { await api.invite(email, 'member'); setEmail(''); load(); } catch (e) { notify('error', (e as Error).message); } }}>{t('invite')}</Button>
              </SpaceBetween>}>{t('users')}</Header>}
              columnDefinitions={[
                { id: 'e', header: t('email'), cell: (u) => u.email },
                { id: 'r', header: t('role'), cell: (u) => u.tenantRole },
              ]} />
          ),
        },
        {
          id: 'groups', label: t('groups'), content: (
            <Table items={groups} header={<Header actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Input value={groupName} placeholder={t('name')} onChange={(e) => setGroupName(e.detail.value)} />
                <Button variant="primary" onClick={async () => { await api.createGroup(groupName); setGroupName(''); load(); }}>{t('createGroup')}</Button>
              </SpaceBetween>}>{t('groups')}</Header>}
              columnDefinitions={[
                { id: 'n', header: t('name'), cell: (g) => g.name },
                {
                  id: 'm', header: t('members'), cell: (g) => (
                    <Multiselect selectedOptions={(members[g.groupId] ?? []).map((s) => ({ value: s, label: emailOf(s) }))}
                      options={users.map((u) => ({ value: u.sub, label: u.email ?? u.sub }))}
                      onChange={(e) => setGroupMembers(g.groupId, e.detail.selectedOptions.map((o) => o.value!))}
                      placeholder={t('addMember')} filteringType="auto" />
                  ),
                },
              ]} />
          ),
        },
        {
          id: 'audit', label: t('audit'), content: (
            <Table items={audit} empty={<Box>–</Box>}
              columnDefinitions={[
                { id: 't', header: 'Time', cell: (a) => fmtDate(a.ts as number) },
                { id: 'u', header: t('user'), cell: (a) => emailOf(String(a.actor)) },
                { id: 'a', header: 'Action', cell: (a) => String(a.action) },
                { id: 'r', header: 'Resource', cell: (a) => String(a.resource) },
              ]} />
          ),
        },
      ]} />
    </ContentLayout>
  );
}
