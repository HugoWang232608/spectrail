import React from 'react'
import ReactDOM from 'react-dom/client'

import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-700.css'
import SourceViewer from './components/SourceViewer'
import { visualFixture, VISUAL_EVIDENCE_FINGERPRINT, VISUAL_TASK_ID } from './visualFixtures'
import './styles/app.css'
import './styles/visual.css'

const fixtureName = new URLSearchParams(window.location.search).get('fixture') ?? 'pdf-0'
const fixture = visualFixture(fixtureName)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <main className="visual-harness" data-visual-fixture={fixtureName}>
      <p className="visual-case-label">{fixture.name}</p>
      <SourceViewer
        taskId={VISUAL_TASK_ID}
        requirement={fixture.requirement}
        blocks={fixture.blocks}
        blocksError={null}
        evidenceFingerprint={VISUAL_EVIDENCE_FINGERPRINT}
        blocksEvidenceFingerprint={VISUAL_EVIDENCE_FINGERPRINT}
      />
    </main>
  </React.StrictMode>
)
