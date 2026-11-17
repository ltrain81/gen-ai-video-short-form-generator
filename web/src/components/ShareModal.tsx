import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import DatePicker from '@cloudscape-design/components/date-picker';
import FormField from '@cloudscape-design/components/form-field';
import Modal from '@cloudscape-design/components/modal';
import Select, { SelectProps } from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from '@cloudscape-design/components/table';
import { useEffect, useMemo, useState } from 'react';
import { useApp } from '../App';
import { api } from '../api/client';
import type { Grant, Group, TenantUser } from '../api/types';
import { useI18n } from '../i18n';
import { fmtDate } from '../util';

interface Props { resource: { type: 'asset' | 'collection'; id: string; name: string }; onClose: () => void }

export default function ShareModal({ resource, onClose }: Props) {
  const { t } = useI18n();
  const { me, notify } = useApp();
  const [grants, setGrants] = useState<Grant[]>([]);
  const [users, setUsers] = useState<TenantUser[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [principal, setPrincipal] = useState<SelectProps.Option | null>(null);
  const [role, setRole] = useState<SelectProps.Option>({ value: 'user', label: t('roleUser') });
  const [expires, setExpires] = useState('');
  const [busy, setBusy] = useState(false);

  const isAsset = resource.type === 'asset';
  const reload = async () => setGrants(await (isAsset ? api.grants(resource.id) : api.collectionGrants(resource.id)));
  useEffect(() => {
    reload().catch((e) => notify('error', e.message));
    api.users().then(setUsers).catch(() => undefined);
    api.groups().then(setGroups).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resource.id]);

  const label = useMemo(() => {
    const byUser = Object.fromEntries(users.map((u) => [`USER#${u.sub}`, u.email ?? u.sub]));
    const byGroup = Object.fromEntries(groups.map((g) => [`GROUP#${g.groupId}`, `${t('group')}: ${g.name}`]));
    return (p: string) => (p.startsWith('TENANT#') ? t('tenantAll') : byUser[p] ?? byGroup[p] ?? p);
  }, [users, groups, t]);

  const options: SelectProps.OptionGroup[] = [
    { label: t('tenantAll'), options: [{ value: `TENANT#${me?.tenantId}`, label: t('tenantAll') }] },
    { label: t('groups'), options: groups.map((g) => ({ value: `GROUP#${g.groupId}`, label: g.name })) },
    { label: t('users'), options: users.filter((u) => u.sub !== me?.sub).map((u) => ({ value: `USER#${u.sub}`, label: u.email ?? u.sub })) },
  ];
  const roles = [
    { value: 'user', label: t('roleUser') },
    { value: 'editor', label: t('roleEditor') },
    { value: 'owner', label: t('roleOwner') },
  ];

  const add = async () => {
    if (!principal?.value) return;
    setBusy(true);
    try {
      const g = { principal: principal.value, role: role.value!, expiresAt: expires ? new Date(expires + 'T23:59:59').getTime() : undefined };
      await (isAsset ? api.putGrant(resource.id, g) : api.putCollectionGrant(resource.id, g));
      setPrincipal(null);
      await reload();
    } catch (e) {
      notify('error', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async (p: string) => {
    try { await (isAsset ? api.deleteGrant(resource.id, p) : api.deleteCollectionGrant(resource.id, p)); await reload(); }
    catch (e) { notify('error', (e as Error).message); }
  };

  return (
    <Modal visible onDismiss={onClose} size="large" header={`${t('share')}: ${resource.name}`}
      footer={<Box float="right"><Button onClick={onClose}>{t('confirm')}</Button></Box>}>
      <SpaceBetween size="l">
        <SpaceBetween direction="horizontal" size="s" alignItems="end">
          <FormField label={t('shareWith')}><div style={{ minWidth: 260 }}><Select expandToViewport selectedOption={principal} onChange={(e) => setPrincipal(e.detail.selectedOption)} options={options} filteringType="auto" placeholder={t('shareWith')} /></div></FormField>
          <FormField label={t('role')}><div style={{ minWidth: 260 }}><Select expandToViewport selectedOption={role} onChange={(e) => setRole(e.detail.selectedOption)} options={roles} /></div></FormField>
          <FormField label={t('expires')}><DatePicker expandToViewport value={expires} onChange={(e) => setExpires(e.detail.value)} placeholder="YYYY/MM/DD" /></FormField>
          <Button variant="primary" loading={busy} disabled={!principal} onClick={add}>{t('addShare')}</Button>
        </SpaceBetween>
        <Table
          variant="embedded"
          items={grants}
          columnDefinitions={[
            { id: 'p', header: t('shareWith'), cell: (g) => label(g.principal) },
            { id: 'r', header: t('role'), cell: (g) => roles.find((r) => r.value === g.role)?.label ?? g.role },
            { id: 'e', header: t('expires'), cell: (g) => (g.expiresAt ? fmtDate(g.expiresAt) : t('never')) },
            { id: 'x', header: '', cell: (g) => (g.role === 'owner' && g.principal === `USER#${me?.sub}` ? null : <Button variant="inline-link" onClick={() => remove(g.principal)}>{t('remove')}</Button>) },
          ]}
        />
      </SpaceBetween>
    </Modal>
  );
}
