// "/": the story page. Plain claims, the failover explained step by step, the written rules, and the limits.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Icon, Logo, type IconName } from '../components/icons'
import { ThemeToggle } from '../components/Theme'
import { Chip } from '../components/ui'

/** Fades a section in when it scrolls into view. */
function Reveal({ children, className = '', delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [on, setOn] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') { setOn(true); return }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setOn(true); io.disconnect() } }, { threshold: 0.15 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return <div ref={ref} className={`reveal ${on ? 'is-in' : ''} ${className}`} style={{ transitionDelay: `${delay}ms` }}>{children}</div>
}

// ---- hero: the mesh, looping through a crash and a carry -----------------------------------------------------
type Phase = 'normal' | 'crash' | 'carry' | 'recover'
const PHASES: { id: Phase; ms: number; caption: string }[] = [
  { id: 'normal', ms: 3200, caption: 'Shoppers add to carts. The nearest node saves each one first, then copies it to the others.' },
  { id: 'crash', ms: 2400, caption: 'Mumbai is killed in the middle of the shopping. Three adds had not been copied yet.' },
  { id: 'carry', ms: 3000, caption: 'The browsers still hold those signed events. They carry them to Pune, which checks every signature.' },
  { id: 'recover', ms: 3000, caption: 'Mumbai restarts, replays its log and catches up. Every node ends with the same state.' },
]

function MeshHero() {
  const [i, setI] = useState(0)
  useEffect(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce) return
    const t = setTimeout(() => setI((n) => (n + 1) % PHASES.length), PHASES[i].ms)
    return () => clearTimeout(t)
  }, [i])
  const phase = PHASES[i]
  const down = phase.id === 'crash' || phase.id === 'carry'
  const toPune = phase.id !== 'normal'
  const N = { sdk: [320, 62], mum: [112, 292], pun: [320, 330], del: [528, 292] } as const
  const line = (a: readonly number[], b: readonly number[], bend = 0) => `M${a[0]} ${a[1]} Q${(a[0] + b[0]) / 2 + bend} ${(a[1] + b[1]) / 2} ${b[0]} ${b[1]}`
  const paths = {
    sdkMum: line(N.sdk, N.mum, -50), sdkPun: line(N.sdk, N.pun, 0), sdkDel: line(N.sdk, N.del, 50),
    mumPun: line(N.mum, N.pun, 0), punDel: line(N.pun, N.del, 0), mumDel: line(N.mum, N.del, 0),
  }
  const Node = ({ id, x, y, name, color }: { id: string; x: number; y: number; name: string; color: string }) => (
    <g className={`mesh__node ${id === 'mum' && down ? 'is-dead' : ''} ${id === 'mum' && phase.id === 'recover' ? 'is-back' : ''}`} transform={`translate(${x} ${y})`}>
      <circle r="38" className="mesh__halo" style={{ fill: color }} />
      <circle r="27" className="mesh__core" style={{ stroke: color }} />
      <g className="mesh__db" style={{ stroke: color }}><ellipse cx="0" cy="-8" rx="11" ry="4.5" /><path d="M-11 -8v14c0 2.5 5 4.5 11 4.5s11-2 11-4.5V-8M-11 -1c0 2.5 5 4.5 11 4.5s11-2 11-4.5" /></g>
      <text y="52" textAnchor="middle" className="mesh__label">{name}</text>
      {id === 'mum' && down && <g className="mesh__x"><circle r="12" cy="-34" cx="26" /><path d="M21 -39l10 10M31 -39l-10 10" /></g>}
    </g>
  )
  // hidden until its own start time, otherwise a delayed packet is drawn at the SVG origin first
  const Packet = ({ d, color, dur, begin = 0 }: { d: string; color: string; dur: number; begin?: number }) => (
    <circle r="4.5" fill={color} className="mesh__packet" visibility="hidden">
      <set attributeName="visibility" to="visible" begin={`${begin}s`} />
      <animateMotion dur={`${dur}s`} begin={`${begin}s`} repeatCount="indefinite" path={d} />
    </circle>
  )
  return (
    <figure className="mesh" aria-label="Animated diagram: a browser, three nodes, and a failover">
      <svg viewBox="0 0 640 400" role="img" aria-hidden="true">
        <g className="mesh__links" fill="none">
          <path d={paths.mumPun} className={down ? 'is-cut' : ''} /><path d={paths.punDel} /><path d={paths.mumDel} className={down ? 'is-cut' : ''} />
          <path d={paths.sdkMum} className={`mesh__sdk ${!toPune ? 'is-hot' : ''} ${down ? 'is-cut' : ''}`} />
          <path d={paths.sdkPun} className={`mesh__sdk ${toPune ? 'is-hot' : ''}`} /><path d={paths.sdkDel} className="mesh__sdk" />
        </g>
        <g key={phase.id}>
          {phase.id === 'normal' && <><Packet d={paths.sdkMum} color="var(--node-mum)" dur={1.6} /><Packet d={paths.sdkMum} color="var(--node-mum)" dur={1.6} begin={0.8} /><Packet d={paths.mumPun} color="var(--teal)" dur={1.8} begin={0.5} /><Packet d={paths.mumDel} color="var(--teal)" dur={2.4} begin={1} /><Packet d={paths.punDel} color="var(--teal)" dur={2} begin={1.4} /></>}
          {phase.id === 'carry' && <><Packet d={paths.sdkPun} color="var(--amber)" dur={1} /><Packet d={paths.sdkPun} color="var(--amber)" dur={1} begin={0.33} /><Packet d={paths.sdkPun} color="var(--amber)" dur={1} begin={0.66} /><Packet d={paths.punDel} color="var(--teal)" dur={1.8} begin={1.2} /></>}
          {phase.id === 'recover' && <><Packet d={paths.sdkPun} color="var(--node-pun)" dur={1.6} /><Packet d={paths.mumPun} color="var(--teal)" dur={1.2} begin={0.3} /><Packet d={paths.mumDel} color="var(--teal)" dur={1.8} begin={0.9} /></>}
        </g>
        <g className="mesh__sdknode" transform={`translate(${N.sdk[0]} ${N.sdk[1]})`}>
          <rect x="-70" y="-26" width="140" height="52" rx="14" /><text y="-4" textAnchor="middle" className="mesh__sdktitle">Browser SDK</text>
          <text y="14" textAnchor="middle" className="mesh__sdksub">{phase.id === 'carry' ? 'carrying 3 signed events' : 'outbox of signed events'}</text>
        </g>
        <Node id="mum" x={N.mum[0]} y={N.mum[1]} name="Mumbai" color="var(--node-mum)" />
        <Node id="pun" x={N.pun[0]} y={N.pun[1]} name="Pune" color="var(--node-pun)" />
        <Node id="del" x={N.del[0]} y={N.del[1]} name="Delhi" color="var(--node-del)" />
      </svg>
      <figcaption><span className={`mesh__phase mesh__phase--${phase.id}`}>{phase.id === 'normal' ? 'All healthy' : phase.id === 'crash' ? 'Node killed' : phase.id === 'carry' ? 'Carry' : 'Recovered'}</span>{phase.caption}</figcaption>
    </figure>
  )
}

// ---- the failover in eight steps ------------------------------------------------------------------------------
const STEPS: { title: string; text: string; from: number; to: number; dashed?: boolean; dead?: boolean }[] = [
  { title: 'The shopper adds an item', text: 'The SDK sends the add with an op_id to the preferred node. The node commits, signs and answers 201 with the signed event. The SDK keeps that event in its outbox.', from: 0, to: 1 },
  { title: 'The node copies it onward', text: 'Peers get the event by push and by pull. When any peer has it, the browser lets it go from the outbox.', from: 1, to: 2 },
  { title: 'The node is killed', text: 'Anything not yet copied now exists only on that node\'s disk and in the browsers\' outboxes.', from: 1, to: 1, dead: true },
  { title: 'The next click times out', text: 'After 600 ms the SDK marks the node down and retries on the next node with the SAME op_id.', from: 0, to: 1, dashed: true, dead: true },
  { title: 'The browser carries its events', text: 'Before the first request on the new node it posts its outbox to /v1/carry. The node checks every hash and the origin\'s signature, then ingests them through the normal gap-safe path.', from: 0, to: 2 },
  { title: 'The shopper sees success', text: 'The one request was a little slower. The judge panel shows: acknowledged = present on survivors.', from: 2, to: 0 },
  { title: 'The dead node restarts', text: 'It replays its log (RECOVERING) and pulls what it missed. Duplicates are ignored by event id; a retried add is merged by the op_id rule.', from: 1, to: 2 },
  { title: 'Every node agrees', text: 'The event digests and state hashes match on all nodes: CONVERGED.', from: 2, to: 1 },
]
const LANES = [{ x: 90, name: 'Browser SDK', c: 'var(--teal)' }, { x: 280, name: 'Mumbai', c: 'var(--node-mum)' }, { x: 470, name: 'Pune', c: 'var(--node-pun)' }]

function FailoverStory() {
  const [step, setStep] = useState(0)
  const [auto, setAuto] = useState(true)
  useEffect(() => {
    if (!auto || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const t = setTimeout(() => setStep((s) => (s + 1) % STEPS.length), 3400)
    return () => clearTimeout(t)
  }, [step, auto])
  const s = STEPS[step]
  return (
    <div className="story">
      <ol className="story__steps">
        {STEPS.map((x, i) => (
          <li key={x.title}><button className={`story__step ${i === step ? 'is-on' : ''} ${i < step ? 'is-past' : ''}`} onClick={() => { setStep(i); setAuto(false) }}>
            <span className="story__n">{i + 1}</span><span><strong>{x.title}</strong><small>{x.text}</small></span></button></li>
        ))}
      </ol>
      <div className="story__diagram card">
        <svg viewBox="0 0 560 420" role="img" aria-label={`Step ${step + 1}: ${s.title}`}>
          {LANES.map((l, i) => (
            <g key={l.name} className={i === 1 && (s.dead || step === 2) ? 'lane is-dead' : 'lane'}>
              <rect x={l.x - 62} y="14" width="124" height="38" rx="12" style={{ stroke: l.c }} /><text x={l.x} y="38" textAnchor="middle">{l.name}</text>
              <line x1={l.x} y1="52" x2={l.x} y2="400" style={{ stroke: l.c }} />
            </g>
          ))}
          {STEPS.slice(0, step + 1).map((x, i) => {
            const y = 86 + i * 38
            const a = LANES[x.from].x, b = LANES[x.to].x
            const cur = i === step
            if (x.from === x.to) return <g key={i} className={`arrow ${cur ? 'is-cur' : ''} ${x.dead ? 'is-dead' : ''}`}><circle cx={a} cy={y} r="9" /><text x={a + 16} y={y + 4}>{x.dead ? 'killed (kill -9)' : ''}</text></g>
            return (
              <g key={i} className={`arrow ${cur ? 'is-cur' : ''} ${x.dashed ? 'is-fail' : ''}`}>
                <line x1={a} y1={y} x2={b} y2={y} strokeDasharray={x.dashed ? '6 5' : undefined} />
                <path d={`M${b} ${y} l${b > a ? -8 : 8} -5 v10z`} /><text x={(a + b) / 2} y={y - 7} textAnchor="middle">{i + 1}</text>
              </g>
            )
          })}
        </svg>
        <p className="story__now"><b>{step + 1}. {s.title}.</b> {s.text}</p>
        <button className="btn btn--ghost btn--xs" onClick={() => setAuto((a) => !a)}><Icon name={auto ? 'pause' : 'play'} size={13} />{auto ? 'Pause' : 'Play'}</button>
      </div>
    </div>
  )
}

// ---- content -----------------------------------------------------------------------------------------------------------
const CLAIMS: { icon: IconName; claim: string; evidence: string; tests: string }[] = [
  { icon: 'shield', claim: '0 errors shown to shoppers while a node is killed', evidence: 'A live counter on the judge panel', tests: 'tests T17, T20' },
  { icon: 'database', claim: '0 acknowledged add-to-carts lost', evidence: '"acknowledged = present on survivors", checked against every node', tests: 'test T17 (kill -9)' },
  { icon: 'scale', claim: 'No concurrent edit silently overwritten', evidence: 'Receipts with both versions, a review queue, a 409 on a stale decision', tests: 'tests T4, T6, T7' },
  { icon: 'eye', claim: 'Status is never greener than the facts', evidence: 'UNKNOWN when a node is unreachable; CONVERGED only with equal digests', tests: 'tests T1, T2, F1' },
]

const IDEAS: { code: string; icon: IconName; title: string; text: string; demo: ReactNode }[] = [
  { code: 'U1', icon: 'scale', title: 'Fail-safe, explainable conflicts', text: 'Every rule picks the option whose mistake costs less. A closed order can never bury a newly added item. Both versions, the rule in plain words, and the supervisor\'s reason are kept and replicated.', demo: <ConflictDemo /> },
  { code: 'U2', icon: 'eye', title: 'Honest consistency, proven live', text: 'Each cart says how far it has spread. A node that cannot be reached is UNKNOWN, never green. "Prove it" ends with identical digests and state hashes on every node.', demo: <BadgeDemo /> },
  { code: 'U3', icon: 'layers', title: 'A graceful-degradation ladder', text: 'Every node shows where it stands: healthy, constrained, isolated or recovering, and what changes at each rung. On a slow link the payment crosses before the backlog.', demo: <LadderDemo /> },
  { code: 'U4', icon: 'gauge', title: 'Zero-loss failover and a data-at-risk meter', text: 'The browser keeps what a node has not copied yet and carries it on failover. A live meter shows exactly what a crash right now would lose.', demo: <RiskDemo /> },
]

const RULES: [string, string, string, string][] = [
  ['Cart owner, note, category', 'The latest edit wins; the other version stays in history', 'No', 'Low stakes, nothing is lost'],
  ['Delivery speed', 'The highest value wins', 'Receipt only', 'A lost upgrade is worse than a false one'],
  ['Order status', 'The most active status wins: Shopping, Checking out, Paid, then Order placed / Cancelled', 'Yes', 'A false "Order placed" can ship an incomplete cart'],
  ['Fulfilment centre', 'The earliest assignment holds and the double dispatch is flagged', 'Yes', 'Only a person redirects a shipment'],
  ['Delivery pin', 'The latest pin wins; pins 200 m or more apart go to review', 'If far', 'A little jitter is harmless, a kilometre is not'],
  ['Cart activity', 'Union of every item added; a retried add counts once', 'n/a', 'Adding never conflicts'],
  ['Order placed vs a new item', 'Status stays "Order placed" and a review opens: ship with it, refund and re-order, or keep', 'Yes', 'An addition is never buried under a close'],
  ['Two supervisors disagree', 'Same value: no conflict. Different: it reopens under the field rule', 'Yes', 'No resolution silently overrides another'],
]

const LIMITS = [
  'A write is lost only if its node dies before any peer has it and no browser outbox holds it (for example the tab was closed). Pushing critical events first shrinks that window; it cannot remove it.',
  'A node holding a valid signing key can still lie. We do not claim Byzantine tolerance.',
  'Localhost traffic is not TLS. A deployed VM needs a TLS reverse proxy.',
  'Revocations issued during a partition are unknown to the cut-off node until it reconnects.',
  'We do not claim 100% uptime. We claim: no error shown while any node answers, and no acknowledged write lost while any browser or peer holds it.',
]

function ConflictDemo() {
  const [flip, setFlip] = useState(false)
  useEffect(() => { const t = setInterval(() => setFlip((f) => !f), 2600); return () => clearInterval(t) }, [])
  return (
    <div className="mini mini--conflict">
      <div className="mini__cands"><span className="cand cand--a">Mumbai: <b>Order placed</b></span><span className="cand cand--b">Pune: <b>Checking out</b></span></div>
      <div className={`mini__result ${flip ? 'is-on' : ''}`}><Icon name="scale" size={14} />Shown on every node: <b>Checking out</b><small>the most active status wins, a supervisor decides</small></div>
    </div>
  )
}
function BadgeDemo() {
  const seq = ['LOCAL_ONLY', 'PARTIAL', 'REPLICATED', 'UNKNOWN'] as const
  const [i, setI] = useState(0)
  useEffect(() => { const t = setInterval(() => setI((n) => (n + 1) % seq.length), 1500); return () => clearInterval(t) }, [seq.length])
  const v = seq[i]
  return (
    <div className="mini"><Chip key={v} tone={v === 'REPLICATED' ? 'ok' : v === 'PARTIAL' ? 'info' : v === 'UNKNOWN' ? 'muted' : 'warn'} icon={v === 'REPLICATED' ? 'check' : v === 'UNKNOWN' ? 'wifioff' : 'database'} className="mini__badge">{v.replace('_', ' ')}</Chip>
      <small className="muted">{v === 'UNKNOWN' ? 'a node cannot be reached: never green' : 'how far this cart has spread'}</small></div>
  )
}
function LadderDemo() {
  const rungs = ['Healthy', 'Constrained', 'Isolated', 'Recovering']
  const [i, setI] = useState(0)
  useEffect(() => { const t = setInterval(() => setI((n) => (n + 1) % rungs.length), 1400); return () => clearInterval(t) }, [rungs.length])
  return <ol className="ladder ladder--mini">{rungs.map((r, k) => <li key={r} className={`ladder__rung ${k === i ? `is-on is-${r.toLowerCase()}` : ''}`}><i />{r}</li>)}</ol>
}
function RiskDemo() {
  const [n, setN] = useState(5)
  useEffect(() => { const t = setInterval(() => setN((x) => (x <= 0 ? 6 : x - 1)), 900); return () => clearInterval(t) }, [])
  return (
    <div className="mini"><div className="meter"><div className="meter__bar"><div className={`meter__fill meter__fill--${n > 3 ? 'bad' : n > 0 ? 'warn' : 'ok'}`} style={{ width: `${(n / 6) * 100}%` }} /></div>
      <div className="meter__text"><span>{n} events only on this node</span><span>{n === 0 ? 'nothing at risk' : 'a crash now would lose them'}</span></div></div></div>
  )
}

// ---- the page ------------------------------------------------------------------------------------------------------------
export default function Landing() {
  return (
    <div className="landing">
      <header className="nav">
        <Link to="/" className="brand"><Logo size={26} /><span>SyncShield</span></Link>
        <nav aria-label="Sections"><a href="#claims">Claims</a><a href="#how">How failover works</a><a href="#ideas">Four ideas</a><a href="#rules">The rules</a><a href="#limits">Limits</a></nav>
        <div className="nav__end"><ThemeToggle /><Link to="/demo" className="btn btn--primary btn--sm">Open console<Icon name="arrow" size={14} /></Link></div>
      </header>

      <section className="hero">
        <div className="hero__copy">
          <p className="eyebrow">CodeAstra 2.0 · Cloud Computing and Distributed Systems · PS2</p>
          <h1>Regions can die.<br /><span className="grad">Shoppers won't notice.</span><br />No add-to-cart is lost.</h1>
          <p className="lead">SyncShield is a data layer for apps that run in several regions. When a whole node dies or the network between nodes breaks, the browser quietly moves to the next node, no acknowledged write is lost, concurrent edits are settled by written safety rules (or a supervisor), and every screen says honestly how far to trust what it shows.</p>
          <div className="hero__cta">
            <Link to="/demo" className="btn btn--primary btn--lg"><Icon name="play" size={18} />Open the live console</Link>
            <a href="#how" className="btn btn--lg">See how failover works</a>
          </div>
          <ul className="hero__facts"><li><Icon name="check" size={15} />Writes accepted on every node, always</li><li><Icon name="check" size={15} />Every event signed by its node</li><li><Icon name="check" size={15} />Retries can never double an item</li></ul>
        </div>
        <MeshHero />
      </section>

      <section id="claims" className="section">
        <Reveal><h2>Four claims. All measured.</h2><p className="section__lead">Nothing here is a promise. Each claim has a counter on the console and a test behind it.</p></Reveal>
        <div className="claims">{CLAIMS.map((c, i) => (
          <Reveal key={c.claim} delay={i * 80} className="claim card"><span className="claim__icon"><Icon name={c.icon} size={22} /></span><h3>{c.claim}</h3><p>{c.evidence}</p><Chip tone="muted" icon="flask">{c.tests}</Chip></Reveal>
        ))}</div>
      </section>

      <section id="how" className="section section--tint">
        <Reveal><h2>What happens when a node dies</h2><p className="section__lead">A node dies holding an event that no peer has yet. The browser still holds that signed event and carries it to a survivor.</p></Reveal>
        <Reveal><FailoverStory /></Reveal>
      </section>

      <section id="ideas" className="section">
        <Reveal><h2>Four ideas that carry the design</h2></Reveal>
        <div className="ideas">{IDEAS.map((x, i) => (
          <Reveal key={x.code} delay={i * 80} className="idea card"><header><span className="idea__code">{x.code}</span><Icon name={x.icon} size={20} /></header><h3>{x.title}</h3><p>{x.text}</p><div className="idea__demo">{x.demo}</div></Reveal>
        ))}</div>
      </section>

      <section id="rules" className="section section--tint">
        <Reveal><h2>The rules are written down</h2><p className="section__lead">Two writes to the same thing conflict only when neither saw the other. A write that saw the old value is just an update. When they do conflict, this table decides, and the engine never uses wall clocks to decide anything that matters.</p></Reveal>
        <Reveal className="table-wrap card">
          <table className="table table--rules"><thead><tr><th>What</th><th>Rule when two nodes disagree</th><th>Review?</th><th>Why it is safe</th></tr></thead>
            <tbody>{RULES.map((r) => <tr key={r[0]}><td><strong>{r[0]}</strong></td><td>{r[1]}</td><td><Chip tone={r[2] === 'Yes' ? 'warn' : r[2] === 'No' ? 'muted' : 'info'}>{r[2]}</Chip></td><td>{r[3]}</td></tr>)}</tbody></table>
        </Reveal>
      </section>

      <section id="limits" className="section">
        <Reveal><h2>What we do not guarantee</h2><p className="section__lead">We say these before a judge asks.</p></Reveal>
        <Reveal><ul className="limits">{LIMITS.map((l) => <li key={l}><Icon name="alert" size={16} />{l}</li>)}</ul></Reveal>
      </section>

      <section className="cta">
        <Reveal><h2>Break it yourself.</h2><p>Kill a node while shoppers are adding. Cut a link and edit the same cart on both sides. Forge an event. The counters will tell you what happened.</p>
          <Link to="/demo" className="btn btn--primary btn--lg"><Icon name="play" size={18} />Open the live console</Link></Reveal>
      </section>

      <footer className="foot"><Logo size={20} /><span>SyncShield · Akshit Sharma, Rahil, Miti, Simrit · TE Computer Engineering, SPIT Mumbai · CodeAstra 2.0 (RGIT)</span>
        <span className="muted">AP with strong eventual consistency · FastAPI · SQLite · Ed25519 · React</span></footer>
    </div>
  )
}
