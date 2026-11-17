import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Form from '@cloudscape-design/components/form';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Link from '@cloudscape-design/components/link';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { FormEvent, useState } from 'react';
import { useAuth } from '../auth';
import { getConfig } from '../config';
import { useI18n } from '../i18n';

export default function LoginPage() {
  const { signIn, signUp, confirm, setNewPassword } = useAuth();
  const selfSignUp = getConfig().selfSignUp !== 'false';
  const { t, lang, setLang } = useI18n();
  const [mode, setMode] = useState<'signin' | 'signup' | 'confirm' | 'newPassword'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      if (mode === 'signin') { if ((await signIn(email, password)) === 'newPassword') { setPassword(''); setMode('newPassword'); } }
      else if (mode === 'newPassword') await setNewPassword(password);
      else if (mode === 'signup') { await signUp(email, password, workspace || email.split('@')[1], lang); setMode('confirm'); }
      else { await confirm(email, code); await signIn(email, password); }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-card">
        <form onSubmit={submit}>
          <Container header={<Header variant="h1" description={t('appSubtitle')}
            actions={<Button variant="inline-link" onClick={() => setLang(lang === 'ko' ? 'en' : 'ko')}>{t('language')}</Button>}>
            {t('appName')}</Header>}>
            <Form actions={<Button variant="primary" loading={busy} formAction="submit">
              {mode === 'signin' ? t('signIn') : mode === 'signup' ? t('signUp') : t('confirm')}</Button>}>
              <SpaceBetween size="m">
                {error && <Alert type="error">{error}</Alert>}
                {mode === 'confirm' && <Alert type="info">{t('codeSent')}</Alert>}
                {mode === 'newPassword' && <Alert type="info">{t('setNewPassword')}</Alert>}
                <FormField label={t('email')}>
                  <Input type="email" value={email} onChange={(e) => setEmail(e.detail.value)} autoFocus disabled={mode === 'confirm' || mode === 'newPassword'} />
                </FormField>
                {mode !== 'confirm' && (
                  <FormField label={t('password')}>
                    <Input type="password" value={password} onChange={(e) => setPassword(e.detail.value)} />
                  </FormField>
                )}
                {mode === 'signup' && (
                  <FormField label={t('workspaceName')}>
                    <Input value={workspace} onChange={(e) => setWorkspace(e.detail.value)} placeholder={email.split('@')[1] ?? ''} />
                  </FormField>
                )}
                {mode === 'confirm' && (
                  <FormField label={t('confirmCode')}>
                    <Input value={code} onChange={(e) => setCode(e.detail.value)} inputMode="numeric" />
                  </FormField>
                )}
                {selfSignUp && (mode === 'signin' || mode === 'signup') && <Box textAlign="center">
                  <Link onFollow={() => setMode(mode === 'signin' ? 'signup' : 'signin')}>
                    {mode === 'signin' ? t('noAccount') : t('haveAccount')}
                  </Link>
                </Box>}
                {!selfSignUp && mode === 'signin' && <Box textAlign="center" color="text-body-secondary" variant="small">{t('inviteOnly')}</Box>}
              </SpaceBetween>
            </Form>
          </Container>
        </form>
      </div>
    </div>
  );
}
