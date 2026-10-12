// Auth state for the shop console: which personas hold a live session, and how to renew it.
//
// Tokens live ONLY in this module's memory. They are never written to localStorage / IndexedDB / the URL,
// and they are not put in any event: a page reload means "sign in again" (the credential and its non-exportable
// key stay in the wallet, so that is one click).
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { REGIONS, AUTH_ENABLED, ISSUER_URL, setTokenProvider } from '../api.js'
import {
  claimCredential, completeLogin, describeCredential, forgetPersona, parseClaimBlock, personas, requestChallenge,
} from './wallet.js'

const AuthCtx = createContext(null)
export const useAuth = () => useContext(AuthCtx)

const RENEW_BEFORE_S = 30

export function AuthProvider({ children }) {
  const sessions = useRef(new Map())        // persona -> {token, exp, role, actor_id, credential_id, auth_mode, region}
  const timers = useRef(new Map())
  const [, bump] = useState(0)
  const refresh = () => bump((n) => n + 1)
  const [names, setNames] = useState([])    // personas that have a credential in this wallet
  const [cards, setCards] = useState({})    // persona -> display-only credential info
  const [active, setActive] = useState(null)
  const activeRef = useRef(null)
  activeRef.current = active
  const [log, setLog] = useState([])
  const consent = useRef(new Set())         // personas whose consent screen was accepted this page load
  const note = (m) => setLog((l) => [m, ...l].slice(0, 6))

  const loadWallet = useCallback(async () => {
    const list = await personas()
    setNames(list)
    const next = {}
    for (const p of list) next[p] = await describeCredential(p).catch(() => null)
    setCards(next)
    setActive((a) => a ?? list[0] ?? null)
  }, [])
  useEffect(() => { if (AUTH_ENABLED) loadWallet() }, [loadWallet])

  const sessionOf = (p) => sessions.current.get(p) ?? null

  // One resolver for api.js and the SDK: by persona name, by {role}, or the active persona. Stable identity.
  const tokenFor = useCallback((who) => {
    const now = Date.now()
    if (who && typeof who === 'object' && who.role) {
      const s = [...sessions.current.values()].find((x) => x.role === who.role && x.exp * 1000 > now)
      return s ? s.token : null
    }
    const s = sessions.current.get(who ?? activeRef.current)
    return s && s.exp * 1000 > now ? s.token : null
  }, [])

  // api.js asks for a token by persona name, by {role}, or for the active persona.
  useEffect(() => {
    if (!AUTH_ENABLED) return undefined
    setTokenProvider(tokenFor)
    return () => setTokenProvider(null)
  }, [tokenFor])

  // Try each region in turn: a region that died before issuing the session simply gets a fresh challenge
  // on the next one (a presentation bound to a dead node's nonce is never reused).
  const loginNow = useCallback(async (persona) => {
    let lastError = null
    for (const r of REGIONS) {
      try {
        const challenge = await requestChallenge(r.url)
        const out = await completeLogin({ persona, regionUrl: r.url, challenge })
        // the sidecar answers with `expires_at` (epoch seconds); keep one name, `exp`, for every consumer
        const session = { ...out, exp: out.expires_at, region: r.id }
        sessions.current.set(persona, session)
        clearTimeout(timers.current.get(persona))
        const ms = Math.max(5_000, (out.expires_at - RENEW_BEFORE_S) * 1000 - Date.now())
        timers.current.set(persona, setTimeout(() => { loginNow(persona).catch(() => {}) }, ms))
        refresh()
        note(`${persona} signed in via ${r.id} as ${out.role} (${out.auth_mode})`)
        return session
      } catch (e) {
        lastError = e
        if (e.status && e.status < 500 && e.status !== 503) break   // a real denial: no point asking another region
      }
    }
    sessions.current.delete(persona)
    refresh()
    note(`${persona}: sign-in failed (${lastError?.body?.error?.code || lastError?.message || 'unknown'})`)
    throw lastError
  }, [])

  const logout = useCallback((persona) => {
    clearTimeout(timers.current.get(persona))
    timers.current.delete(persona)
    sessions.current.delete(persona)
    consent.current.delete(persona)
    refresh()
    note(`${persona} signed out (token discarded)`)
  }, [])

  const claimBlock = useCallback(async (text) => {
    const entries = parseClaimBlock(text)
    const results = []
    for (const { persona, code } of entries) {
      try {
        const out = await claimCredential({ persona, code, issuerUrl: ISSUER_URL })
        results.push({ persona, ok: true, role: out.role })
      } catch (e) {
        results.push({ persona, ok: false, error: e?.body?.error?.code || e.message })
      }
    }
    await loadWallet()
    return results
  }, [loadWallet])

  const remove = useCallback(async (persona) => {
    logout(persona)
    await forgetPersona(persona)
    await loadWallet()
  }, [logout, loadWallet])

  useEffect(() => () => { for (const t of timers.current.values()) clearTimeout(t) }, [])

  // Rebuilt on every render on purpose: sessions live in a ref, and a re-login that replaces a session must be
  // visible to consumers even though the number of sessions did not change.
  const value = {
    enabled: AUTH_ENABLED, names, cards, active, setActive, sessionOf, tokenFor, loginNow, logout, claimBlock, remove,
    consent: consent.current, log,
    live: [...sessions.current.entries()].filter(([, s]) => s.exp * 1000 > Date.now()).map(([p, s]) => ({ persona: p, ...s })),
  }

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>
}
