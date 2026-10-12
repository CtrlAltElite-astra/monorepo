// Wallet: holds credentials, shows the consent screen, and sends only the approved claims.
import { useEffect, useState } from 'react';
import { BRAND, CLAIM_LABELS, ALL_CLAIMS, URLS } from '@true/shared';
import { createHolderProof, createPresentation, readCredential } from '@true/credentials';
import {
  addCredential, addHistory, getCredentials, getHistory, getHolderKey, removeCredential,
  type HistoryEntry, type StoredCredential,
} from './storage.ts';

// The page is chosen by the part of the address after '#', e.g. #/present?request_uri=...
function useRoute() {
  const read = () => {
    const [path, query = ''] = window.location.hash.replace(/^#/, '').split('?');
    return { path: path || '/', params: new URLSearchParams(query) };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return route;
}

const goHome = () => { window.location.hash = '#/'; };

export default function App() {
  const route = useRoute();
  return (
    <div className="mx-auto min-h-screen max-w-md bg-slate-50 shadow-xl">
      <header className="bg-indigo-700 px-5 py-4 text-white">
        <button onClick={goHome} className="text-left">
          <div className="text-2xl font-bold tracking-wide">{BRAND.name} <span className="text-sm font-normal opacity-80">Wallet</span></div>
          <div className="text-xs opacity-80">{BRAND.tagline}</div>
        </button>
      </header>
      <main className="p-5">
        {route.path === '/claim' && <Claim initialCode={route.params.get('code') ?? ''} />}
        {route.path === '/present' && <Present requestUri={route.params.get('request_uri') ?? ''} />}
        {route.path === '/' && <Home />}
      </main>
    </div>
  );
}

// ---------- Home: credential cards, add buttons, history ----------

function Home() {
  const [credentials, setCredentials] = useState<StoredCredential[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [link, setLink] = useState('');

  const reload = () => { getCredentials().then(setCredentials); getHistory().then(setHistory); };
  useEffect(reload, []);

  // A pasted verifier link looks like http://localhost:5173/#/present?request_uri=...  Keep the part after '#'.
  const openLink = () => { if (link.includes('#')) window.location.hash = link.slice(link.indexOf('#')); };

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase text-slate-500">My credentials</h2>
        {credentials.length === 0 && <p className="rounded-xl bg-white p-4 text-sm text-slate-500">No credentials yet. Ask your institution for a claim code.</p>}
        {credentials.map((c) => <Card key={c.id} stored={c} onRemove={() => removeCredential(c.id).then(reload)} />)}
        <a href="#/claim" className="block rounded-xl bg-indigo-600 py-3 text-center font-medium text-white">+ Add credential with a claim code</a>
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold uppercase text-slate-500">Answer a verifier</h2>
        <p className="text-xs text-slate-500">Paste the link shown under the verifier's QR code.</p>
        <div className="flex gap-2">
          <input value={link} onChange={(e) => setLink(e.target.value)} placeholder="Verifier link" className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <button onClick={openLink} className="rounded-lg bg-slate-800 px-4 text-sm text-white">Open</button>
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold uppercase text-slate-500">History (stored only on this device)</h2>
        {history.length === 0 && <p className="text-sm text-slate-500">Nobody has asked for anything yet.</p>}
        {history.map((h, i) => (
          <div key={i} className="rounded-lg bg-white p-3 text-sm">
            <div className="flex justify-between">
              <span className="font-medium">{h.verifierName}</span>
              <span className={h.decision === 'shared' ? 'text-emerald-600' : 'text-rose-600'}>{h.decision}</span>
            </div>
            <div className="text-xs text-slate-500">{h.claims.map((c) => CLAIM_LABELS[c] ?? c).join(', ')}</div>
            <div className="text-xs text-slate-400">{new Date(h.at).toLocaleString()} · {h.outcome}</div>
          </div>
        ))}
      </section>
    </div>
  );
}

function Card({ stored, onRemove }: { stored: StoredCredential; onRemove: () => void }) {
  const [claims, setClaims] = useState<Record<string, unknown> | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { readCredential(stored.credential).then(setClaims); }, [stored.credential]);
  if (!claims) return null;
  return (
    <div className="rounded-2xl bg-gradient-to-br from-indigo-600 to-violet-700 p-4 text-white shadow">
      <div className="flex items-center gap-3">
        <img src={String(claims.photo)} alt="" className="h-14 w-14 rounded-lg" />
        <div className="min-w-0">
          <div className="truncate text-lg font-semibold">{String(claims.name)}</div>
          <div className="truncate text-xs opacity-80">{stored.issuerName}</div>
          <div className="text-xs opacity-80">Student credential</div>
        </div>
      </div>
      <button onClick={() => setOpen(!open)} className="mt-3 text-xs underline opacity-90">{open ? 'Hide' : 'Show'} everything inside this card</button>
      {open && (
        <div className="mt-2 space-y-1 rounded-lg bg-white/10 p-3 text-xs">
          {ALL_CLAIMS.filter((name) => name !== 'photo').map((name) => (
            <div key={name} className="flex justify-between gap-3">
              <span className="opacity-70">{CLAIM_LABELS[name]}</span>
              <span className="text-right">{String(claims[name])}</span>
            </div>
          ))}
          <button onClick={onRemove} className="pt-2 text-rose-200 underline">Remove from wallet</button>
        </div>
      )}
    </div>
  );
}

// ---------- Claim: turn a one-time code into a credential ----------

function Claim({ initialCode }: { initialCode: string }) {
  const [code, setCode] = useState(initialCode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function claim() {
    setBusy(true);
    setError('');
    try {
      const cleaned = code.trim().toUpperCase();
      // 1. Ask the issuer for this code's nonce.
      const offerResponse = await fetch(`${URLS.issuerApi}/v1/claim-codes/${encodeURIComponent(cleaned)}`);
      const offer = await offerResponse.json();
      if (!offerResponse.ok) throw new Error(offer.error.message);
      // 2. Prove we own our key by signing that nonce. The private key never leaves this device.
      const holder = await getHolderKey();
      const proof = await createHolderProof({
        holderPrivateKey: holder.privateKey, holderPublicJwk: holder.publicJwk, nonce: offer.nonce, audience: offer.issuerId,
      });
      // 3. Swap the code + proof for a signed credential.
      const response = await fetch(`${URLS.issuerApi}/v1/credentials`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ code: cleaned, proof }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error.message);
      await addCredential(result.credential, result.issuerName);
      goHome();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    }
    setBusy(false);
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Add a credential</h2>
      <p className="text-sm text-slate-600">Enter the one-time claim code from your institution. Credentials cannot be typed in by hand: only the issuer can create one.</p>
      <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="CLAIM CODE" className="w-full rounded-lg border border-slate-300 px-3 py-3 text-center font-mono text-xl tracking-widest uppercase" />
      {error && <p className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">Refused: {error}</p>}
      <button onClick={claim} disabled={busy || !code.trim()} className="w-full rounded-xl bg-indigo-600 py-3 font-medium text-white disabled:opacity-40">{busy ? 'Working…' : 'Add to wallet'}</button>
      <button onClick={goHome} className="w-full text-sm text-slate-500">Cancel</button>
    </div>
  );
}

// ---------- Present: consent screen ----------

type RequestObject = {
  requestId: string; verifierId: string; verifierName: string; purpose: string;
  claims: string[]; nonce: string; responseUri: string; expiresAt: string;
};

function Present({ requestUri }: { requestUri: string }) {
  const [request, setRequest] = useState<RequestObject | null>(null);
  const [stored, setStored] = useState<StoredCredential | null>(null);
  const [claims, setClaims] = useState<Record<string, unknown>>({});
  const [status, setStatus] = useState<'loading' | 'ready' | 'sending' | 'done' | 'error'>('loading');
  const [message, setMessage] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const response = await fetch(requestUri);
        const body = await response.json();
        if (!response.ok) throw new Error(body.error.message);
        setRequest(body);
        // Use the first credential in the wallet (the demo has one credential type).
        const first = (await getCredentials())[0];
        if (!first) throw new Error('You have no credential in this wallet yet.');
        setStored(first);
        setClaims(await readCredential(first.credential));
        setStatus('ready');
      } catch (e) {
        setMessage(e instanceof Error ? e.message : 'Could not load the request');
        setStatus('error');
      }
    })();
  }, [requestUri]);

  async function answer(approve: boolean) {
    if (!request || !stored) return;
    setStatus('sending');
    let body: object = { declined: true };
    if (approve) {
      const holder = await getHolderKey();
      // Only the requested claims go in. The proof is signed for this verifier and this nonce only.
      const presentation = await createPresentation({
        credential: stored.credential, holderPrivateKey: holder.privateKey,
        claims: request.claims, nonce: request.nonce, audience: request.verifierId,
      });
      body = { presentation };
    }
    const response = await fetch(request.responseUri, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    const result = await response.json();
    const outcome = response.ok ? result.state : result.error.code;
    await addHistory({ at: new Date().toISOString(), verifierName: request.verifierName, claims: request.claims, decision: approve ? 'shared' : 'declined', outcome });
    setMessage(approve ? (outcome === 'verified' ? 'Shared. The verifier accepted your proof.' : `The verifier did not accept it (${result.code ?? outcome}).`) : 'You declined. Nothing was shared.');
    setStatus('done');
  }

  if (status === 'loading') return <p className="text-sm text-slate-500">Loading request…</p>;
  if (status === 'error') return <Notice text={message} tone="bad" />;
  if (status === 'done') return <Notice text={message} tone={message.startsWith('Shared') ? 'good' : 'neutral'} />;
  if (!request) return null;

  const hidden = ALL_CLAIMS.filter((name) => !request.claims.includes(name));
  return (
    <div className="space-y-4">
      <div className="rounded-xl bg-white p-4">
        <div className="text-xs uppercase text-slate-500">Request from</div>
        <div className="text-xl font-semibold">{request.verifierName}</div>
        <div className="text-sm text-slate-600">Purpose: {request.purpose}</div>
        <div className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">Verifier identity is not checked yet (added in the next milestone).</div>
      </div>

      <div className="rounded-xl border-2 border-emerald-200 bg-emerald-50 p-4">
        <div className="mb-2 text-sm font-semibold text-emerald-800">Will be shared</div>
        {request.claims.map((name) => (
          <div key={name} className="flex items-center justify-between gap-3 py-1 text-sm">
            <span>{CLAIM_LABELS[name] ?? name}</span>
            {name === 'photo'
              ? <img src={String(claims[name])} alt="" className="h-8 w-8 rounded" />
              : <span className="font-medium">{claims[name] === true ? 'Yes' : claims[name] === false ? 'No' : String(claims[name])}</span>}
          </div>
        ))}
      </div>

      <div className="rounded-xl bg-slate-200 p-4">
        <div className="mb-2 text-sm font-semibold text-slate-700">Will NOT be shared</div>
        <div className="text-sm text-slate-600">{hidden.map((name) => CLAIM_LABELS[name]).join(' · ')}</div>
      </div>

      <div className="flex gap-3">
        <button onClick={() => answer(false)} disabled={status === 'sending'} className="flex-1 rounded-xl border border-slate-300 bg-white py-3 font-medium">Decline</button>
        <button onClick={() => answer(true)} disabled={status === 'sending'} className="flex-1 rounded-xl bg-emerald-600 py-3 font-medium text-white">{status === 'sending' ? 'Sending…' : 'Approve'}</button>
      </div>
    </div>
  );
}

function Notice({ text, tone }: { text: string; tone: 'good' | 'bad' | 'neutral' }) {
  const colour = tone === 'good' ? 'bg-emerald-50 text-emerald-800' : tone === 'bad' ? 'bg-rose-50 text-rose-800' : 'bg-slate-200 text-slate-700';
  return (
    <div className="space-y-4">
      <p className={`rounded-xl p-4 ${colour}`}>{text}</p>
      <button onClick={goHome} className="w-full rounded-xl bg-indigo-600 py-3 font-medium text-white">Back to wallet</button>
    </div>
  );
}
