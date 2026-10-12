// "API view": the raw last response per node, always on, so nobody has to trust the UI's reading of it.
import { useState } from 'react'
import { useDemo } from '../state/demo'
import type { RegionId } from '../api/types'
import { Chip, Json, NODE_NAME, NodeDot, Segmented } from './ui'

export function ApiPanel() {
  const d = useDemo()
  const [region, setRegion] = useState<RegionId>(d.joined[0] ?? 'mum')
  const [which, setWhich] = useState<'status' | 'reports'>('status')
  const rec = d.last[region]?.[which]
  return (
    <div className="panel api">
      <div className="api__bar">
        <Segmented size="sm" value={region} onChange={setRegion} options={d.joined.map((r) => ({ id: r, label: NODE_NAME[r] }))} />
        <Segmented size="sm" value={which} onChange={setWhich} options={[{ id: 'status', label: '/v1/status' }, { id: 'reports', label: '/v1/reports' }]} />
        {rec && <><Chip tone={rec.status < 300 ? 'ok' : 'bad'}>{rec.status}</Chip><span className="muted">{rec.ms} ms · {new Date(rec.at).toLocaleTimeString()}</span></>}
      </div>
      {rec ? <><p className="api__path"><NodeDot id={region} size={8} /> {rec.method} {rec.path}</p><Json value={rec.body} /></> : <p className="muted">No response yet from {NODE_NAME[region]}.</p>}
    </div>
  )
}
