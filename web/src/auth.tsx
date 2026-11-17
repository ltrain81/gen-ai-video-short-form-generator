import { Amplify } from 'aws-amplify';
import { confirmSignIn, confirmSignUp, fetchAuthSession, getCurrentUser, signIn, signOut, signUp } from 'aws-amplify/auth';
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';
import { getConfig } from './config';

export function configureAuth() {
  const c = getConfig();
  Amplify.configure({
    Auth: { Cognito: { userPoolId: c.userPoolId, userPoolClientId: c.userPoolClientId } },
    ...(c.eventsHttpDomain ? {
      API: { Events: { endpoint: `https://${c.eventsHttpDomain}/event`, region: c.region, defaultAuthMode: 'userPool' as const } },
    } : {}),
  });
}

export async function accessToken(): Promise<string> {
  const session = await fetchAuthSession();
  const token = session.tokens?.accessToken?.toString();
  if (!token) throw new Error('Not signed in');
  return token;
}

interface AuthState {
  user: { username: string; userId: string } | null;
  loading: boolean;
  /** Returns 'newPassword' when an invited user must set a password first. */
  signIn: (email: string, password: string) => Promise<'ok' | 'newPassword'>;
  setNewPassword: (password: string) => Promise<void>;
  signUp: (email: string, password: string, workspace: string, language: string) => Promise<void>;
  confirm: (email: string, code: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState>(null as never);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthState['user']>(null);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try { setUser(await getCurrentUser()); } catch { setUser(null); } finally { setLoading(false); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const value: AuthState = {
    user, loading,
    signIn: async (email, password) => {
      const r = await signIn({ username: email, password });
      if (r.nextStep.signInStep === 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED') return 'newPassword';
      if (!r.isSignedIn) throw new Error(`Additional step required: ${r.nextStep.signInStep}`);
      await refresh();
      return 'ok';
    },
    setNewPassword: async (password) => {
      const r = await confirmSignIn({ challengeResponse: password });
      if (!r.isSignedIn) throw new Error(`Additional step required: ${r.nextStep.signInStep}`);
      await refresh();
    },
    signUp: async (email, password, workspace, language) => {
      await signUp({ username: email, password, options: { userAttributes: { email, 'custom:tenantName': workspace, 'custom:tenantLanguage': language } } });
    },
    confirm: async (email, code) => { await confirmSignUp({ username: email, confirmationCode: code }); },
    signOut: async () => { await signOut(); setUser(null); },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
